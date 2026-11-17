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
from .gateway.asaas import Asaas, GatewayErro, GatewayParcial
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


# ----------------------------------------------------------------------------------------------- uso diário
@login_required
@permission_required("cobranca.add_cobranca", raise_exception=True)
def gerar_pelo_gateway(request, pk):
    """Emite boleto ou PIX (dinâmico, com baixa automática) para uma conta a receber."""
    from apps.financeiro.models import ContaReceber

    titulo = get_object_or_404(ContaReceber, pk=pk)
    cfg = servico.config_ativa(titulo.empresa)
    if titulo.status in ("pago", "cancelado"):
        messages.error(request, "Título já está pago ou cancelado.")
        return redirect(titulo.get_absolute_url())
    if request.method != "POST":
        return render(request, "cobranca/gerar.html", {"titulo": "Gerar cobrança", "icone": "receipt", "conta": titulo,
                                                       "cfg": cfg, "voltar": titulo.get_absolute_url()})
    tipo = request.POST.get("tipo")
    if tipo not in ("pix", "boleto", "pix_estatico"):
        tipo = "pix"
    if tipo == "pix_estatico" or cfg is None:
        cob = Cobranca.objects.create(conta_receber=titulo, tipo="pix", valor=titulo.saldo, vencimento=titulo.vencimento)
        messages.success(request, "Cobrança gerada (PIX estático, sem baixa automática).")
        return redirect(cob.get_absolute_url())
    cob = Cobranca.objects.create(conta_receber=titulo, tipo=tipo, valor=titulo.saldo, vencimento=titulo.vencimento)
    try:
        servico.emitir(cob)
    except GatewayParcial as exc:
        # já existe no gateway: NÃO se apaga (ficaria um boleto pagável sem registro aqui); o usuário rebusca os dados
        messages.warning(request, f"Cobrança emitida, mas não foi possível obter o boleto/QR agora ({exc}). "
                                  "Use “Consultar no gateway” em instantes.")
        return redirect(cob.get_absolute_url())
    except GatewayErro as exc:
        cob.delete()                      # nada foi criado no gateway
        messages.error(request, f"Não foi possível emitir pelo gateway: {exc}")
        return redirect(titulo.get_absolute_url())
    messages.success(request, f"{'Boleto' if tipo == 'boleto' else 'PIX'} emitido. O pagamento será baixado automaticamente.")
    return redirect(cob.get_absolute_url())


@login_required
@permission_required("cobranca.change_cobranca", raise_exception=True)
@require_POST
def atualizar_do_gateway(request, pk):
    cob = get_object_or_404(Cobranca, pk=pk)
    try:
        cfg = servico.config_ativa(cob.conta_receber.empresa)
        if cob.gateway_id and cfg:
            p = Asaas(cfg).consultar(cob.gateway_id)
            if p.get("status") == "RECEIVED":
                messages.success(request, f"Consulta ao gateway: {servico.registrar_pagamento(cfg, cob.pk, p)}.")
            else:
                cob.gateway_status = p.get("status", cob.gateway_status)
                cob.save(update_fields=["gateway_status"])
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
        lista.append({"c": c, "url": f"{settings.ERP_URL_BASE or request.build_absolute_uri('/').rstrip('/')}/cobranca/webhook/asaas/",
                      "token": c.obter_token(), "eventos": c.eventos.all()[:10]})
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
        Asaas(cfg).testar()
        messages.success(request, "Conexão com o gateway OK: a chave foi aceita.")
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
