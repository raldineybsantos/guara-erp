# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.urls import path

from . import views

app_name = "identidade"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("entrar/<slug:slug>/", views.entrar, name="entrar"),
    path("retorno/<slug:slug>/", views.retorno, name="retorno"),
    path("ldap/novo/", views.ldap_form, name="ldap_novo"),
    path("ldap/<int:pk>/", views.ldap_form, name="ldap_editar"),
    path("ldap/<int:pk>/testar/", views.ldap_testar, name="ldap_testar"),
    path("oidc/novo/", views.oidc_form, name="oidc_novo"),
    path("oidc/<int:pk>/", views.oidc_form, name="oidc_editar"),
    path("<str:tipo>/<int:pk>/excluir/", views.excluir, name="excluir"),
]
