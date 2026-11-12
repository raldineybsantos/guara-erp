from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.crud import BootstrapMixin
from apps.financeiro.models import ContaPagar

from .models import Instancia, Processo


class NovaSolicitacaoForm(BootstrapMixin, forms.Form):
    processo = forms.ModelChoiceField(Processo.objects.filter(ativo=True))
    titulo = forms.CharField(label="Título", max_length=200)
    descricao = forms.CharField(label="Descrição", widget=forms.Textarea, required=False)


class AcaoForm(BootstrapMixin, forms.Form):
    acao = forms.ChoiceField(choices=[("concluir", "Concluir"), ("aprovar", "Aprovar"),
                                      ("rejeitar", "Rejeitar"), ("cancelar", "Cancelar")])
    comentario = forms.CharField(label="Comentário", widget=forms.Textarea, required=False)


@login_required
def minhas_tarefas(request):
    pendentes = Instancia.objects.pendentes_para(request.user).select_related("processo", "etapa_atual", "solicitante")
    minhas = Instancia.objects.filter(solicitante=request.user).select_related("processo", "etapa_atual")[:50]
    return render(request, "workflow/minhas_tarefas.html", {
        "titulo": "Minhas tarefas", "icone": "check2-square", "pendentes": pendentes, "minhas": minhas,
    })


@login_required
@permission_required("workflow.add_instancia", raise_exception=True)
def nova_solicitacao(request):
    form = NovaSolicitacaoForm(request.POST or None, initial={"processo": request.GET.get("processo")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            inst = d["processo"].iniciar(d["titulo"], request.user, d["descricao"])
            messages.success(request, f"Solicitação {inst} aberta.")
            return redirect(inst.get_absolute_url())
        except ValidationError as e:
            form.add_error(None, e)
    return render(request, "form_simples.html", {"form": form, "titulo": "Nova solicitação", "icone": "plus-circle"})


@login_required
def agir(request, pk):
    inst = get_object_or_404(Instancia.objects.visiveis_para(request.user), pk=pk)
    form = AcaoForm(request.POST or None)
    if request.method != "POST":
        return redirect(inst.get_absolute_url())
    if form.is_valid():
        try:
            inst.avancar(request.user, form.cleaned_data["acao"], form.cleaned_data["comentario"])
            messages.success(request, "Ação registrada.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
    return redirect(inst.get_absolute_url())


@login_required
@permission_required(["workflow.add_instancia", "financeiro.view_contapagar"], raise_exception=True)
def iniciar_aprovacao_pagar(request, pk):
    """Envia uma conta a pagar para o processo 'aprovacao-pagamento'."""
    conta = get_object_or_404(ContaPagar, pk=pk)
    processo = Processo.objects.filter(codigo="aprovacao-pagamento", ativo=True).first()
    if not processo:
        messages.error(request, "Cadastre um processo ativo com código 'aprovacao-pagamento' "
                                "(ou rode `python manage.py setup_erp`).")
        return redirect(conta.get_absolute_url())
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Enviar para aprovação", "voltar": conta.get_absolute_url(),
            "mensagem": f"Abrir solicitação de aprovação de pagamento para “{conta}” ({conta.fornecedor})?",
        })
    inst = processo.iniciar(f"Aprovar pagamento: {conta}"[:200], request.user,
                            f"Fornecedor: {conta.fornecedor}\nValor: R$ {conta.valor_total}\n"
                            f"Vencimento: {conta.vencimento:%d/%m/%Y}", objeto=conta)
    messages.success(request, f"Solicitação {inst} aberta.")
    return redirect(inst.get_absolute_url())
