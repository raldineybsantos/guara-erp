import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.crud import BootstrapMixin
from apps.fiscal import cofre

from . import catalogo, ia, servico, telegram
from .models import ConfiguracaoIA, SolicitacaoCadastro, VinculoCanal

USAR = "assistente.add_solicitacaocadastro"


def _rotulos(sc):
    """[(rótulo, valor legível)] da proposta, com nomes no lugar dos IDs de relação."""
    crud = catalogo.crud_de(sc.alvo)
    if not crud:
        return []
    form = catalogo.formulario(crud)
    linhas = []
    for nome, valor in sc.dados.items():
        f = form.fields.get(nome)
        if f is None:
            continue
        if isinstance(f, forms.ModelChoiceField):
            obj = f.queryset.filter(pk=valor).first()
            valor = f.label_from_instance(obj) if obj else valor
        elif getattr(f, "choices", None):
            valor = dict((str(k), v) for k, v in f.choices).get(str(valor), valor)
        linhas.append((f.label or nome, valor))
    return linhas


@login_required
@permission_required(USAR, raise_exception=True)
def conversa(request):
    config = ConfiguracaoIA.atual()
    if request.method == "POST":
        try:
            servico.interpretar(request.user, request.POST.get("texto", ""))
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return redirect("assistente:conversa")
    pedidos = list(SolicitacaoCadastro.objects.filter(usuario=request.user)[:15])
    for p in pedidos:
        p.linhas = _rotulos(p)
    return render(request, "assistente/conversa.html", {
        "titulo": "Assistente de IA", "icone": "stars", "config": config, "pedidos": pedidos,
        "exemplos": [n for _, n, _ in catalogo.disponiveis(request.user)][:8],
        "vinculo": VinculoCanal.objects.filter(usuario=request.user).first()})


@login_required
@require_POST
def descartar(request, pk):
    SolicitacaoCadastro.objects.filter(pk=pk, usuario=request.user).update(status="descartada")
    return redirect("assistente:conversa")


@login_required
@permission_required(USAR, raise_exception=True)
@require_POST
def vincular(request):
    v, _ = VinculoCanal.objects.get_or_create(usuario=request.user)
    if request.POST.get("desvincular"):
        v.chat_id = ""
        v.save(update_fields=["chat_id"])
        messages.success(request, "Telegram desvinculado.")
    else:
        messages.info(request, f"No Telegram, envie ao bot da empresa: /vincular {v.novo_codigo()} "
                               "(vale por 10 minutos e só uma vez).")
    return redirect("assistente:conversa")


class ConfigForm(BootstrapMixin, forms.ModelForm):
    nova_chave = forms.CharField(label="chave de API", required=False, widget=forms.PasswordInput(render_value=False),
                                 help_text="Em branco = manter a atual. Não é necessária no Ollama.")
    novo_token = forms.CharField(label="token do bot do Telegram", required=False,
                                 widget=forms.PasswordInput(render_value=False), help_text="Em branco = manter o atual.")

    class Meta:
        model = ConfiguracaoIA
        fields = ["provedor", "url_base", "modelo", "tempo_limite", "telegram_ativo"]

    def clean_url_base(self):
        url = self.cleaned_data["url_base"].strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValidationError("Comece por http:// ou https://")
        return url


@login_required
@permission_required("assistente.change_configuracaoia", raise_exception=True)
def configuracao(request):
    config = ConfiguracaoIA.atual()
    form = ConfigForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        if form.cleaned_data["nova_chave"]:
            c.chave_api = cofre.cifrar(form.cleaned_data["nova_chave"].encode())
        if form.cleaned_data["novo_token"]:
            c.telegram_token = cofre.cifrar(form.cleaned_data["novo_token"].encode())
        c.save()
        if request.POST.get("testar"):
            try:
                ia.extrair_json(ia.chamar(c, 'Responda somente com o JSON {"ok": true}.', "teste"))
                messages.success(request, "Conexão com a IA funcionando.")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
        else:
            messages.success(request, "Configuração salva.")
        return redirect("assistente:configuracao")
    webhook = request.build_absolute_uri("/assistente/telegram/webhook/")
    return render(request, "assistente/configuracao.html", {
        "titulo": "Configuração da IA", "icone": "sliders", "form": form, "config": config, "webhook": webhook})


@csrf_exempt
@require_POST
def telegram_webhook(request):
    config = ConfiguracaoIA.atual()
    if not config.telegram_ativo or not telegram.segredo_confere(
            config, request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        return HttpResponseForbidden()
    if len(request.body) > 20000:
        return HttpResponse(status=200)
    try:
        update = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=200)
    chat_id, resposta = telegram.tratar(config, update if isinstance(update, dict) else {})
    if chat_id and resposta:
        telegram.enviar(config, chat_id, resposta)
    return HttpResponse(status=200)


