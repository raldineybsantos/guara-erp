import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect

from apps.core.models import LogAuditoria

from .models import Documento, Versao


@login_required
@permission_required("ged.view_documento", raise_exception=True)
def download(request, pk):
    versao = get_object_or_404(Versao.objects.select_related("documento__pasta"), pk=pk)
    if not Documento.visiveis_para(request.user).filter(pk=versao.documento_id).exists():
        raise Http404
    try:
        arquivo = versao.arquivo.open("rb")
    except FileNotFoundError:
        raise Http404("Arquivo não encontrado no armazenamento.")
    LogAuditoria.registrar("acao", versao.documento, descricao=f"Download de {versao}")
    tipo = mimetypes.guess_type(versao.nome_original)[0] or "application/octet-stream"
    inline = request.GET.get("ver") == "1"
    return FileResponse(arquivo, as_attachment=not inline, filename=versao.nome_original or None, content_type=tipo)


@login_required
@permission_required("ged.change_documento", raise_exception=True)
def nova_versao(request, pk):
    doc = get_object_or_404(Documento.visiveis_para(request.user), pk=pk)
    if request.method == "POST" and request.FILES.get("arquivo"):
        v = doc.adicionar_versao(request.FILES["arquivo"], request.POST.get("comentario", ""), request.user)
        messages.success(request, f"Versão {v.numero} enviada.")
    else:
        messages.error(request, "Selecione um arquivo.")
    return redirect(doc.get_absolute_url())
