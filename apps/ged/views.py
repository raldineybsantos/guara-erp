from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect

from apps.core.models import LogAuditoria
from apps.core.validators import validar_arquivo_seguro

from .models import Documento, Versao

TIPOS_INLINE = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8"}


@login_required
@permission_required("ged.view_documento", raise_exception=True)
def download(request, pk):
    versao = get_object_or_404(Versao.objects.select_related("documento__pasta"), pk=pk)
    if not Documento.visiveis_para(request.user).filter(pk=versao.documento_id).exists():
        raise Http404
    if versao.av_situacao == "infectado":
        LogAuditoria.registrar("seguranca", versao.documento, descricao=f"Download BLOQUEADO (ameaça detectada): {versao}")
        messages.error(request, "Este arquivo foi marcado pelo antivírus como ameaça e não pode ser baixado. Avise o administrador.")
        return redirect(versao.documento.get_absolute_url())
    try:
        arquivo = versao.arquivo.open("rb")
    except FileNotFoundError:
        raise Http404("Arquivo não encontrado no armazenamento.")
    LogAuditoria.registrar("acao", versao.documento, descricao=f"Download de {versao}")
    # Visualização no navegador só para tipos inofensivos; o resto é sempre baixado.
    inline = request.GET.get("ver") == "1" and versao.extensao in TIPOS_INLINE
    tipo = TIPOS_INLINE.get(versao.extensao, "application/octet-stream")
    resp = FileResponse(arquivo, as_attachment=not inline, filename=versao.nome_original or None, content_type=tipo)
    if versao.extensao != ".pdf":  # o leitor de PDF do Chrome não funciona com sandbox
        resp["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
@permission_required("ged.change_documento", raise_exception=True)
def nova_versao(request, pk):
    doc = get_object_or_404(Documento.visiveis_para(request.user), pk=pk)
    if request.method == "POST" and request.FILES.get("arquivo"):
        try:
            validar_arquivo_seguro(request.FILES["arquivo"])
        except ValidationError as e:
            messages.error(request, e.messages[0])
            return redirect(doc.get_absolute_url())
        try:
            v = doc.adicionar_versao(request.FILES["arquivo"], request.POST.get("comentario", ""), request.user)
        except ValidationError as e:        # antivírus: ameaça ou verificação impossível
            messages.error(request, e.messages[0])
            return redirect(doc.get_absolute_url())
        messages.success(request, f"Versão {v.numero} enviada.")
    else:
        messages.error(request, "Selecione um arquivo.")
    return redirect(doc.get_absolute_url())
