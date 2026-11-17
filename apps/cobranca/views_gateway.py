# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
import hmac
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required, user_passes_test
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.models import LogAuditoria
from apps.core.signals import ip_da_requisicao

from .gateway import servico
from .gateway import efi as efi_mod
from .gateway.base import GatewayErro, GatewayParcial
from .models import Cobranca, ConfiguracaoGateway, EventoGateway

LIMITE_CORPO = 200 * 1024


# ----------------------------------------------------------------------------------------------- webhook
@csrf_exempt
@require_POST
def webhook_asaas(request):
    """Recebe os avisos do Asaas. Autentica pelo header asaas-access-token (comparação em tempo constante)."""
    recebido = request.headers.get("asaas-access-token", "")
    cfg = None
    for c in ConfiguracaoGateway.objects.filter(provedor="asaas", ativo=True):   # percorre todas: tempo não revela qual existe
        token = c.obter_token()
        if token and hmac.compare_digest(recebido.encode(), token.encode()):
            cfg = c
    if cfg is None:
        LogAuditoria.objects.create(usuario=None, acao="seguranca", modelo="cobranca.webhook", objeto_id="",
                                    descricao="Webhook do gateway recusado: token inválido", ip=ip_da_requisicao(request))
        return HttpResponse(status=401)
    if len(request.body) > LIMITE_CORPO:
        return HttpResponse(status=413)
    try:
        corpo = json.loads(request.body or b"{}")
        if not isinstance(corpo, dict):
            raise ValueError
    except ValueError:
        return HttpResponse(status=400)
    evento_id = str(corpo.get("id") or "")[:200]
    tipo = str(corpo.get("event") or "")[:60]
    if not evento_id:
        return HttpResponse(status=400)
    try:
        with transaction.atomic():
            ev = EventoGateway.objects.create(config=cfg, evento_id=evento_id, tipo=tipo,
                                              pagamento_id=str((corpo.get("payment") or {}).get("id", ""))[:60])
    except IntegrityError:
        return JsonResponse({"received": True, "duplicado": True})        # reenvio: já tratado
    try:
        ev.resultado = servico.processar_evento(cfg, corpo)[:300]
        ev.save(update_fields=["resultado"])
    except Exception as exc:
        ev.delete()                          # libera para o reenvio do gateway tentar de novo
        from apps.core.logs import registrar_erro

        registrar_erro(exc, request=request, mensagem=f"Webhook do gateway ({tipo})")
        return HttpResponse(status=500)
    return JsonResponse({"received": True})


@csrf_exempt
@require_POST
def webhook_efi(request, segredo):
    """Aviso da Efí: o POST traz só um token ('notification'). O segredo no caminho identifica a conta; o ERP consulta o
    token na API (GET /v1/notification/:token) e reconsulta cada cobrança antes de qualquer baixa."""
    cfg = None
    for c in ConfiguracaoGateway.objects.filter(provedor="efi", ativo=True):
        t = c.obter_token()
        if t and hmac.compare_digest(segredo.encode(), t.encode()):
            cfg = c
    if cfg is None:
        LogAuditoria.objects.create(usuario=None, acao="seguranca", modelo="cobranca.webhook", objeto_id="",
                                    descricao="Aviso da Efí recusado: segredo inválido", ip=ip_da_requisicao(request))
        return HttpResponse(status=404)
    if len(request.body) > LIMITE_CORPO:             # ler o corpo antes de acessar request.POST
        return HttpResponse(status=413)
    token = str(request.POST.get("notification", ""))[:200]
    if not token:
        return HttpResponse(status=400)
    try:
        api = servico.driver(cfg)
        eventos = api.eventos(token)
    except GatewayErro as exc:
        if exc.status in (400, 401, 403, 404):       # token inexistente/forjado: não adianta a Efí repetir
            return HttpResponse(status=200)
        return HttpResponse(status=500)              # indisponível: a Efí tenta de novo
    resultados = []
    for ev in eventos:
        if not isinstance(ev, dict):
            continue
        evento_id = f"{token}:{ev.get('id')}"[:200]
        charge = str((ev.get("identifiers") or {}).get("charge_id", ""))[:60]
        if not charge or ev.get("type") != "charge":
            continue
        try:
            with transaction.atomic():
                reg = EventoGateway.objects.create(config=cfg, evento_id=evento_id, tipo=str((ev.get("status") or {}).get("current", ""))[:60],
                                                   pagamento_id=charge)
        except IntegrityError:
            continue                                  # já tratado
        try:
            cob = Cobranca.objects.filter(gateway_provedor="efi", gateway_id=charge,
                                          conta_receber__empresa=cfg.empresa).first()
            if cob is None:
                reg.resultado = "cobrança desconhecida"
            else:
                pag = api.pagamento(charge, efi_mod.data_do_evento(ev))
                reg.resultado = servico.aplicar(cfg, cob, pag)[:300]
            reg.save(update_fields=["resultado"])
        except Exception as exc:
            reg.delete()                              # libera para a repetição
            from apps.core.logs import registrar_erro

            registrar_erro(exc, request=request, mensagem="Aviso da Efí")
            return HttpResponse(status=500)
    return JsonResponse({"received": True})


# ----------------------------------------------------------------------------------------------- uso diário
@login_required
@permission_required("cobranca.add_cobranca", raise_exception=True)
def gerar_pelo_gateway(request, pk):
    """Emite boleto ou PIX (dinâmico, com baixa automática) para uma conta a receber."""
    from apps.financeiro.models import ContaReceber

    titulo = get_object_or_404(ContaReceber, pk=pk)
    cfg = servico.config_ativa(titulo.empresa)
    opcoes = []                                    # (rótulo, tipo, provedor) oferecidos conforme os gateways ativos
    for c in ConfiguracaoGateway.objects.filter(empresa=titulo.empresa, ativo=True).order_by("pk"):
        api = servico.DRIVERS[c.provedor]
        if "boleto" in api.TIPOS:
            opcoes.append((f"Boleto registrado — {c.get_provedor_display()}", "boleto", c.provedor, "baixa automática"))
        if "pix" in api.TIPOS:
            opcoes.append((f"PIX com QR Code — {c.get_provedor_display()}", "pix", c.provedor, "baixa automática"))
        if "cartao" in api.TIPOS:
            opcoes.append((f"Cartão de crédito (link de pagamento) — {c.get_provedor_display()}", "cartao", c.provedor,
                           "o cliente digita o cartão na página do gateway"))
    if titulo.status in ("pago", "cancelado"):
        messages.error(request, "Título já está pago ou cancelado.")
        return redirect(titulo.get_absolute_url())
    if request.method != "POST":
        return render(request, "cobranca/gerar.html", {"titulo": "Gerar cobrança", "icone": "receipt", "conta": titulo,
                                                       "cfg": cfg, "opcoes": opcoes, "voltar": titulo.get_absolute_url()})
    tipo = request.POST.get("tipo")
    provedor = ""
    if request.POST.get("opcao") and tipo != "pix_estatico":
        tipo, _, provedor = request.POST["opcao"].partition("|")
    if tipo not in ("pix", "boleto", "cartao", "pix_estatico"):
        tipo = "pix"
    if tipo == "pix_estatico" or cfg is None:
        cob = Cobranca.objects.create(conta_receber=titulo, tipo="pix", valor=titulo.saldo, vencimento=titulo.vencimento)
        messages.success(request, "Cobrança gerada (PIX estático, sem baixa automática).")
        return redirect(cob.get_absolute_url())
    cob = Cobranca.objects.create(conta_receber=titulo, tipo=tipo, valor=titulo.saldo, vencimento=titulo.vencimento)
    try:
        servico.emitir(cob, provedor)
    except GatewayParcial as exc:
        # já existe no gateway: NÃO se apaga (ficaria um boleto pagável sem registro aqui); o usuário rebusca os dados
        messages.warning(request, f"Cobrança emitida, mas não foi possível obter o boleto/QR agora ({exc}). "
                                  "Use “Consultar no gateway” em instantes.")
        return redirect(cob.get_absolute_url())
    except GatewayErro as exc:
        cob.delete()                      # nada foi criado no gateway
        messages.error(request, f"Não foi possível emitir pelo gateway: {exc}")
        return redirect(titulo.get_absolute_url())
    nome = {"boleto": "Boleto", "pix": "PIX", "cartao": "Link de cartão"}[tipo]
    messages.success(request, f"{nome} emitido. O pagamento será baixado automaticamente.")
    return redirect(cob.get_absolute_url())


@login_required
@permission_required("cobranca.change_cobranca", raise_exception=True)
@require_POST
def atualizar_do_gateway(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    try:
        cfg = servico.config_da(cob)
        if cob.gateway_id and cfg:
            res = servico.aplicar(cfg, cob, servico.driver(cfg).pagamento(cob.gateway_id))
            cob.refresh_from_db()
            if cob.status == "paga":
                messages.success(request, f"Consulta ao gateway: {res}.")
            else:
                servico.atualizar_dados(cob)
                messages.success(request, f"Atualizado. Situação no gateway: {cob.gateway_status}.")
    except GatewayErro as exc:
        messages.error(request, f"Falha ao consultar o gateway: {exc}")
    return redirect(cob.get_absolute_url())


@login_required
@permission_required("cobranca.change_cobranca", raise_exception=True)
def cancelar_cobranca(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": "Cancelar cobrança", "voltar": cob.get_absolute_url(),
                                                  "mensagem": "Cancelar esta cobrança? O boleto/PIX deixará de poder ser pago."})
    try:
        servico.cancelar_no_gateway(cob)
        messages.success(request, "Cobrança cancelada.")
    except GatewayErro as exc:
        messages.error(request, f"Não foi possível cancelar: {exc}")
    return redirect(cob.get_absolute_url())


# ----------------------------------------------------------------------------------------------- configuração (superusuário)
def _admin(u):
    return u.is_superuser


@login_required
@user_passes_test(_admin)
def gateways(request):
    from django.conf import settings

    lista = []
    for c in ConfiguracaoGateway.objects.select_related("empresa", "conta_bancaria"):
        base = settings.ERP_URL_BASE or request.build_absolute_uri('/').rstrip('/')
        lista.append({"c": c, "url": f"{base}/cobranca/webhook/asaas/", "token": c.obter_token(),
                      "efi": c.provedor == "efi", "url_efi": servico.url_aviso(c) or "(defina ERP_URL_BASE com https://)",
                      "eventos": c.eventos.all()[:10]})
    return render(request, "cobranca/gateways.html", {"titulo": "Gateway de pagamento", "icone": "credit-card-2-front",
                                                      "lista": lista, "producao_liberada": settings.COBRANCA_GATEWAY_PRODUCAO})


@login_required
@user_passes_test(_admin)
def gateway_form(request, pk=None):
    from .forms_gateway import GatewayForm

    obj = get_object_or_404(ConfiguracaoGateway, pk=pk) if pk else None
    form = GatewayForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        salvo = form.save()
        messages.success(request, f"{salvo} salvo. Cadastre o webhook no painel do gateway com o endereço e o token abaixo.")
        return redirect("cobranca:gateways")
    return render(request, "form_simples.html", {"titulo": "Gateway de pagamento", "icone": "credit-card-2-front", "form": form})


@login_required
@user_passes_test(_admin)
@require_POST
def gateway_testar(request, pk):
    cfg = get_object_or_404(ConfiguracaoGateway, pk=pk)
    try:
        servico.driver(cfg).testar()
        messages.success(request, "Conexão com o gateway OK: as credenciais foram aceitas.")
    except GatewayErro as exc:
        messages.error(request, f"Falha no teste: {exc}")
    return redirect("cobranca:gateways")


@login_required
@user_passes_test(_admin)
@require_POST
def gateway_novo_token(request, pk):
    cfg = get_object_or_404(ConfiguracaoGateway, pk=pk)
    cfg.novo_token_webhook()
    cfg.save()
    messages.success(request, "Novo token gerado. Atualize-o no painel do gateway, senão os avisos serão recusados.")
    return redirect("cobranca:gateways")


@login_required
@user_passes_test(_admin)
@require_POST
def gateway_sincronizar(request):
    r = servico.sincronizar()
    messages.success(request, f"Conciliação: {r['baixadas']} baixa(s), {r['canceladas']} cancelamento(s), {r['erros']} erro(s).")
    return redirect("cobranca:gateways")
