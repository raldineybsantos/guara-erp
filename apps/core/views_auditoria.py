"""Auditoria: histórico por registro, verificação de integridade, tratamento dos logs de erro e página 500."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.apps import apps as django_apps
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from . import acoes, auditoria
from .crud import BootstrapMixin
from .models import LogAuditoria, LogErro, SeloAuditoria
from .signals import _auditado


@login_required
@permission_required("core.view_logauditoria", raise_exception=True)
def historico(request, app, modelo, pk):
    """Tudo o que a trilha registra sobre um registro: quem fez o quê e quando, com os valores antes e depois."""
    try:
        model = django_apps.get_model(app, modelo)
    except LookupError:
        raise Http404 from None
    if not _auditado(model):
        raise Http404
    logs = LogAuditoria.objects.filter(modelo=f"{app}.{modelo}", objeto_id=str(pk)).order_by("data", "id")
    obj = model._default_manager.filter(pk=pk).first()
    return render(request, "core/historico.html", {
        "titulo": f"Histórico: {obj or f'{model._meta.verbose_name} {pk} (excluído)'}", "icone": "clock-history",
        "logs": logs, "obj": obj, "voltar": obj.get_absolute_url() if obj and hasattr(obj, "get_absolute_url") else None})


@login_required
@permission_required("core.view_logauditoria", raise_exception=True)
def integridade(request):
    resultado = None
    if request.method == "POST":
        resultado = auditoria.verificar()
        LogAuditoria.objects.create(
            acao="acao", modelo="core.logauditoria",
            descricao=("Verificou a integridade da trilha: íntegra" if resultado["ok"] else
                       f"Verificou a integridade da trilha: {len(resultado['problemas'])} problema(s)"),
            alteracoes={"registros": resultado["registros"], "selos": resultado["selos"],
                        "problemas": resultado["problemas"][:20]})
    ultimo = SeloAuditoria.objects.first()
    return render(request, "core/auditoria_integridade.html", {
        "titulo": "Integridade da auditoria", "icone": "shield-lock", "resultado": resultado,
        "total": LogAuditoria.objects.count(), "selos": SeloAuditoria.objects.count(), "ultimo": ultimo,
        "sem_selo": LogAuditoria.objects.filter(id__gt=ultimo.ate_id if ultimo else 0).count()})


class TratamentoForm(BootstrapMixin, forms.Form):
    observacao = forms.CharField(label="observação", widget=forms.Textarea(attrs={"rows": 3}), required=False,
                                 help_text="O que foi feito ou por que está sendo ignorado.")


def _tratar(situacao, titulo, ok, icone):
    @login_required
    @permission_required("core.change_logerro", raise_exception=True)
    def view(request, pk):
        erro = get_object_or_404(LogErro, pk=pk)
        return acoes.formulario(request, erro, TratamentoForm, f"{titulo}: {erro.tipo}",
                                lambda d: erro.tratar(situacao, request.user, d["observacao"]), ok, icone=icone,
                                initial={"observacao": erro.observacao})
    return view


erro_resolver = _tratar("resolvido", "Marcar como resolvido", "Erro marcado como resolvido.", "check2-circle")
erro_ignorar = _tratar("ignorado", "Ignorar", "Erro ignorado: novas ocorrências voltam a abrir um registro.",
                       "eye-slash")
erro_analisar = _tratar("investigando", "Colocar em análise", "Erro em análise.", "search")


def erro_500(request):
    """Página de erro com o código que liga a reclamação do usuário ao registro técnico."""
    return render(request, "500.html", {"codigo": getattr(request, "requisicao_id", "")}, status=500)
