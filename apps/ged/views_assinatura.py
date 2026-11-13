"""Telas de assinatura digital e verificação de assinaturas do GED."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.uploadhandler import MemoryFileUploadHandler
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from apps.core.crud import BootstrapMixin
from apps.core.models import LogAuditoria
from apps.core.signals import ip_da_requisicao

from . import assinatura as ass
from .models import Assinatura, Documento, Versao
from .signals import documento_assinado

PFX_MAX_BYTES = 100 * 1024
MOTIVOS = ["Concordo com o conteúdo deste documento", "Sou parte deste contrato", "Aprovo este documento",
           "Assino como testemunha", "Revisei este documento", "Assino como representante legal da empresa"]


class AssinarForm(BootstrapMixin, forms.Form):
    certificado = forms.FileField(label="Certificado digital A1 (.pfx ou .p12)",
                                  widget=forms.ClearableFileInput(attrs={"accept": ".pfx,.p12"}))
    senha = forms.CharField(label="Senha do certificado", widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
                            strip=False)
    motivo = forms.ChoiceField(label="Motivo da assinatura", choices=[(m, m) for m in MOTIVOS])
    local = forms.CharField(label="Local", max_length=120, required=False, initial="São Paulo, SP")
    confirmo = forms.BooleanField(label="Li o documento e confirmo que desejo assiná-lo digitalmente")

    def clean_certificado(self):
        arq = self.cleaned_data["certificado"]
        if arq.size > PFX_MAX_BYTES:
            raise forms.ValidationError("Arquivo grande demais para um certificado A1.")
        if not arq.name.lower().endswith((".pfx", ".p12")):
            raise forms.ValidationError("Envie o arquivo .pfx ou .p12 do certificado.")
        return arq


def _versao_visivel(user, pk):
    versao = get_object_or_404(Versao.objects.select_related("documento"), pk=pk)
    if not Documento.visiveis_para(user).filter(pk=versao.documento_id).exists():
        raise Http404
    return versao


def _proximo(request, padrao):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return padrao


@csrf_exempt
@login_required
def assinar(request, pk):
    # O certificado é lido SÓ em memória: sem o manipulador de arquivo temporário, um upload
    # nunca é gravado em disco. (O CSRF é conferido logo abaixo, depois de trocar o manipulador.)
    request.upload_handlers = [MemoryFileUploadHandler(request)]
    return _assinar(request, pk)


@csrf_protect
def _assinar(request, pk):
    if not request.user.has_perm("ged.assinar_documento"):
        raise PermissionDenied
    versao = _versao_visivel(request.user, pk)
    doc = versao.documento
    if versao != doc.versao_atual:
        messages.warning(request, "Atenção: esta não é a versão mais recente do documento.")
    eh_pdf = versao.extensao == ".pdf"
    form = AssinarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        pfx = form.cleaned_data["certificado"].read()
        senha = form.cleaned_data["senha"]
        try:
            chave, cert, cadeia, dados = ass.carregar_pfx(pfx, senha)
            ass.conferir_titular(request.user, dados)
            with versao.arquivo.open("rb") as f:
                original = f.read()
            if ass.sha256(original) != versao.hash_sha256:
                raise ass.ErroAssinatura("O arquivo armazenado não confere com o hash registrado. Assinatura cancelada.")
            registro = Assinatura(
                documento=doc, versao_origem=versao, usuario=request.user, titular=dados.titular,
                documento_titular=dados.documento, emissor=dados.emissor, serial=dados.serial,
                certificado_valido_ate=dados.valido_ate, icp_brasil=dados.icp_brasil,
                motivo=form.cleaned_data["motivo"], local=form.cleaned_data["local"], ip=ip_da_requisicao(request),
            )
            if eh_pdf:
                assinado = ass.assinar_pdf(original, pfx, senha, ass.nome_campo_unico(original),
                                           registro.motivo, registro.local)
                nova = doc.adicionar_versao(ContentFile(assinado, name=versao.nome_original),
                                            f"Assinado digitalmente por {dados.titular}", request.user)
                registro.tipo, registro.versao_resultante, registro.hash_sha256 = "pades", nova, ass.sha256(assinado)
                registro.save()
            else:
                p7s = ass.assinar_cades(original, chave, cert, cadeia)
                registro.tipo, registro.hash_sha256 = "cades", ass.sha256(original)
                registro.save()
                registro.arquivo_p7s.save(f"{versao.nome_original}.{registro.pk}.p7s", ContentFile(p7s), save=True)
        except ass.ErroAssinatura as e:
            form.add_error(None, str(e))
        else:
            LogAuditoria.registrar("acao", doc, descricao=f"Assinatura digital ({registro.get_tipo_display()}) por "
                                   f"{dados.titular} [{dados.documento}]")
            documento_assinado.send(sender=Documento, documento=doc, assinatura=registro)
            for aviso in dados.avisos:
                messages.warning(request, aviso)
            messages.success(request, f"Documento assinado digitalmente por {dados.titular}.")
            return redirect(_proximo(request, doc.get_absolute_url()))
        finally:
            pfx = senha = chave = None  # descarta o material sensível o quanto antes
    return render(request, "ged/assinar.html", {
        "form": form, "versao": versao, "doc": doc, "eh_pdf": eh_pdf, "titulo": "Assinar digitalmente",
        "icone": "pen", "next": _proximo(request, ""), "uma_coluna": True,
    })


@login_required
def verificar(request, pk):
    versao = _versao_visivel(request.user, pk)
    with versao.arquivo.open("rb") as f:
        conteudo = f.read()
    resultados = ass.verificar_pdf(conteudo) if versao.extensao == ".pdf" else []
    for a in versao.assinaturas.filter(tipo="cades"):
        if a.arquivo_p7s:
            with a.arquivo_p7s.open("rb") as f:
                resultados += ass.verificar_cades(conteudo, f.read())
    return render(request, "ged/verificar.html", {
        "versao": versao, "doc": versao.documento, "resultados": resultados, "titulo": "Verificar assinaturas",
        "icone": "patch-check", "hash": ass.sha256(conteudo), "confere_hash": ass.sha256(conteudo) == versao.hash_sha256,
    })


@login_required
def baixar_p7s(request, pk):
    a = get_object_or_404(Assinatura, pk=pk, tipo="cades")
    if not Documento.visiveis_para(request.user).filter(pk=a.documento_id).exists() or not a.arquivo_p7s:
        raise Http404
    resp = FileResponse(a.arquivo_p7s.open("rb"), as_attachment=True,
                        filename=f"{a.versao_origem.nome_original}.p7s", content_type="application/pkcs7-signature")
    resp["X-Content-Type-Options"] = "nosniff"
    return resp
