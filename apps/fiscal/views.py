from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from apps.core.crud import BootstrapMixin

from .importador import importar_nfe
from .models import ConfiguracaoFiscal, NotaFiscal


class ImportarXmlForm(BootstrapMixin, forms.Form):
    arquivos = forms.FileField(label="Arquivo XML da NF-e",
                               widget=forms.ClearableFileInput(attrs={"accept": ".xml"}))
    gerar_titulo = forms.BooleanField(label="Gerar conta a pagar automaticamente", required=False, initial=True)


@login_required
@permission_required("fiscal.view_notafiscal", raise_exception=True)
def baixar_xml(request, pk):
    nota = get_object_or_404(NotaFiscal, pk=pk)
    if not nota.xml:
        raise Http404
    resp = FileResponse(nota.xml.open("rb"), as_attachment=True, filename=f"{nota.chave_acesso or nota.pk}.xml",
                        content_type="application/xml")
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
@permission_required("fiscal.add_notafiscal", raise_exception=True)
def importar_xml(request):
    form = ImportarXmlForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            nota = importar_nfe(request.FILES["arquivos"])
            if form.cleaned_data["gerar_titulo"]:
                nota.gerar_titulo()
            messages.success(request, f"NF-e importada: {nota} de {nota.fornecedor}.")
            return redirect(nota.get_absolute_url())
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
        except Exception as exc:
            form.add_error(None, f"Não foi possível importar: {exc}")
    return render(request, "form_simples.html", {"form": form, "titulo": "Importar XML de NF-e (entrada)",
                                                 "icone": "upload", "multipart": True})


def _acao_nota(request, pk, func, titulo, msg_confirmar):
    nota = get_object_or_404(NotaFiscal, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "mensagem": msg_confirmar.format(nota=nota),
                                                  "voltar": nota.get_absolute_url()})
    try:
        messages.success(request, func(nota))
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect(nota.get_absolute_url())


@login_required
@permission_required("fiscal.emitir_notafiscal", raise_exception=True)
def emitir_nota(request, pk):
    def f(nota):
        nota.emitir()
        return f"Nota {nota} emitida. Transmita à SEFAZ/prefeitura pelo seu emissor e informe chave e protocolo."
    return _acao_nota(request, pk, f, "Emitir nota fiscal", "Numerar e marcar {nota} como emitida?")


@login_required
@permission_required("fiscal.emitir_notafiscal", raise_exception=True)
def cancelar_nota(request, pk):
    def f(nota):
        if nota.situacao_fiscal == "autorizada":
            raise ValueError("Nota autorizada no fisco: use “Cancelar no fisco”, com a justificativa.")
        nota.status = "cancelada"
        nota.save()
        return "Nota cancelada."
    return _acao_nota(request, pk, f, "Cancelar nota fiscal", "Cancelar a nota {nota}?")


@login_required
@permission_required("fiscal.change_notafiscal", raise_exception=True)
def gerar_titulo_nota(request, pk):
    def f(nota):
        if nota.status == "cancelada":
            raise ValueError("Nota cancelada.")
        t = nota.gerar_titulo()
        return f"Título criado: {t} ({t._meta.verbose_name})."
    return _acao_nota(request, pk, f, "Gerar título financeiro",
                      "Criar conta a receber/pagar no valor total de {nota}?")


@login_required
@permission_required("fiscal.view_notafiscal", raise_exception=True)
def apuracao(request):
    """Resumo mensal de impostos das notas emitidas (base para apuração)."""
    linhas = (NotaFiscal.objects.filter(status="emitida")
              .annotate(mes=TruncMonth("data_emissao")).values("mes", "tipo")
              .annotate(qtd=Count("id"), total=Sum("valor_total"), icms=Sum("valor_icms"), ipi=Sum("valor_ipi"),
                        pis=Sum("valor_pis"), cofins=Sum("valor_cofins"), iss=Sum("valor_iss"))
              .order_by("-mes", "tipo"))
    return render(request, "fiscal/apuracao.html", {"titulo": "Apuração de impostos", "icone": "calculator",
                                                    "linhas": linhas})


# --------------------------------------------------------------------------- transmissão ao fisco
@login_required
@permission_required("fiscal.emitir_notafiscal", raise_exception=True)
def transmitir_nota(request, pk):
    from . import transmissao

    nota = get_object_or_404(NotaFiscal, pk=pk)
    if request.method != "POST":
        return render(request, "confirmar.html", {
            "titulo": "Transmitir ao fisco", "voltar": nota.get_absolute_url(),
            "mensagem": f"Assinar com o certificado da empresa e transmitir {nota}?"})
    try:
        t = transmissao.transmitir(nota, request.user)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        (messages.success if t.resultado == "autorizado" else messages.error)(request, f"{t.codigo} {t.mensagem}")
    return redirect(nota.get_absolute_url())


class JustificativaForm(BootstrapMixin, forms.Form):
    justificativa = forms.CharField(min_length=15, max_length=255, widget=forms.Textarea,
                                    help_text="Mínimo de 15 caracteres. Vai para o fisco.")


@login_required
@permission_required("fiscal.emitir_notafiscal", raise_exception=True)
def cancelar_fiscal(request, pk):
    from apps.core import acoes

    from . import transmissao

    nota = get_object_or_404(NotaFiscal, pk=pk)
    return acoes.formulario(request, nota, JustificativaForm, f"Cancelar {nota} no fisco",
                            lambda d: transmissao.cancelar(nota, d["justificativa"], request.user),
                            lambda t: f"{t.codigo} {t.mensagem}", "cloud-slash")


class CertificadoForm(BootstrapMixin, forms.Form):
    arquivo = forms.FileField(label="certificado A1 (.pfx ou .p12)")
    senha = forms.CharField(widget=forms.PasswordInput(render_value=False))


@csrf_exempt
@login_required
def enviar_certificado(request, pk):
    # Como na assinatura do GED: o .pfx é lido só em memória, nunca vira arquivo temporário em disco.
    request.upload_handlers = [MemoryFileUploadHandler(request)]
    return _enviar_certificado(request, pk)


@csrf_protect
def _enviar_certificado(request, pk):
    if not request.user.has_perm("fiscal.change_configuracaofiscal"):
        raise PermissionDenied
    config = get_object_or_404(ConfiguracaoFiscal, pk=pk)
    form = CertificadoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        arq = form.cleaned_data["arquivo"]
        try:
            if arq.size > 100 * 1024:
                raise ValidationError("Arquivo grande demais para ser um certificado A1.")
            dados = config.guardar_certificado(arq.read(), form.cleaned_data["senha"])
        except ValidationError as e:
            form.add_error("arquivo", e)
        else:
            messages.success(request, f"Certificado de {dados.titular} guardado no cofre, válido até "
                                      f"{dados.valido_ate:%d/%m/%Y}.")
            return redirect(config.get_absolute_url())
    return render(request, "form_simples.html", {
        "form": form, "titulo": f"Certificado A1 — {config.empresa}", "icone": "shield-lock", "multipart": True})
