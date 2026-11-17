# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.urls import path

from . import scim, views

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
    path("saml/<slug:slug>/entrar/", views.saml_entrar, name="saml_entrar"),
    path("saml/<slug:slug>/acs/", views.saml_acs, name="saml_acs"),
    path("saml/<slug:slug>/metadata/", views.saml_metadata, name="saml_metadata"),
    path("saml/novo/", views.saml_form, name="saml_novo"),
    path("saml/<int:pk>/", views.saml_form, name="saml_editar"),
    path("scim/token/novo/", views.token_novo, name="token_novo"),
    path("scim/token/<int:pk>/revogar/", views.token_revogar, name="token_revogar"),
    path("<str:tipo>/<int:pk>/excluir/", views.excluir, name="excluir"),
]
