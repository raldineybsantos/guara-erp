# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
import threading

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import connection
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.models import LogAuditoria

from . import backup
from .models import RegistroBackup


def _admin(u):
    return u.is_superuser


@login_required
@user_passes_test(_admin)
def painel(request):
    pasta = backup.pasta_backup()
    arquivos = sorted((p for p in pasta.iterdir() if backup.PADRAO_NOME.match(p.name)), reverse=True)
    espaco = None
    try:
        import shutil

        espaco = shutil.disk_usage(pasta).free
    except OSError:
        pass
    return render(request, "seguranca/backups.html", {
        "titulo": "Backups", "icone": "cloud-arrow-up",
        "registros": RegistroBackup.objects.all()[:40], "em_andamento": RegistroBackup.objects.filter(situacao="executando").exists(),
        "pasta": pasta, "copia_dir": backup.pasta_copia(), "copia_cmd": bool(getattr(settings, "BACKUP_COMANDO_COPIA", "")),
        "senha_propria": backup.senha_propria(), "n_arquivos": len(arquivos), "espaco": espaco,
        "retencao": (settings.BACKUP_RETENCAO_DIARIOS, settings.BACKUP_RETENCAO_SEMANAIS, settings.BACKUP_RETENCAO_MENSAIS),
        "hora": settings.BACKUP_HORA, "disponiveis": {p.name for p in arquivos}})


def _em_segundo_plano(usuario):
    try:
        backup.executar("manual", usuario)
    finally:
        connection.close()


@login_required
@user_passes_test(_admin)
@require_POST
def executar(request):
    if RegistroBackup.objects.filter(situacao="executando").exists():
        messages.warning(request, "Já existe um backup em andamento.")
    else:
        threading.Thread(target=_em_segundo_plano, args=(request.user,), daemon=True).start()
        messages.success(request, "Backup iniciado. Atualize a página em instantes para ver o resultado.")
    return redirect("seguranca:backups")


def _arquivo(nome):
    if not backup.PADRAO_NOME.match(nome):
        raise Http404
    caminho = backup.pasta_backup() / nome
    if not caminho.is_file():
        raise Http404
    return caminho


@login_required
@user_passes_test(_admin)
def baixar(request, nome):
    caminho = _arquivo(nome)
    LogAuditoria.objects.create(usuario=request.user, acao="acao", modelo="seguranca.backup", objeto_id=nome,
                                descricao=f"Download do backup {nome}")
    return FileResponse(open(caminho, "rb"), as_attachment=True, filename=nome, content_type="application/octet-stream")


@login_required
@user_passes_test(_admin)
@require_POST
def verificar(request, nome):
    caminho = _arquivo(nome)
    try:
        m = backup.verificar(caminho)
        messages.success(request, f"{nome} está íntegro: banco com {m['migracoes']} migração(ões) e {m['arquivos_media']} arquivo(s).")
    except backup.BackupErro as exc:
        messages.error(request, f"{nome} NÃO passou na verificação: {exc}")
    return redirect("seguranca:backups")
