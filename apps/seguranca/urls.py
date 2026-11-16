# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.urls import path

from . import views, views_antivirus, views_backup

app_name = "seguranca"

urlpatterns = [
    path("2fa/", views.painel, name="mfa_painel"),
    path("2fa/ativar/", views.ativar, name="mfa_ativar"),
    path("2fa/verificar/", views.verificar, name="mfa_verificar"),
    path("2fa/codigos/", views.novos_codigos, name="mfa_codigos"),
    path("2fa/desativar/", views.desativar, name="mfa_desativar"),
    path("2fa/usuarios/", views.usuarios, name="mfa_usuarios"),
    path("2fa/usuarios/<int:pk>/redefinir/", views.redefinir, name="mfa_redefinir"),
    path("backups/", views_backup.painel, name="backups"),
    path("backups/executar/", views_backup.executar, name="backup_executar"),
    path("backups/<str:nome>/baixar/", views_backup.baixar, name="backup_baixar"),
    path("backups/<str:nome>/verificar/", views_backup.verificar, name="backup_verificar"),
    path("antivirus/", views_antivirus.painel, name="antivirus"),
    path("antivirus/testar/", views_antivirus.testar, name="antivirus_testar"),
    path("antivirus/varrer/", views_antivirus.varrer, name="antivirus_varrer"),
]
