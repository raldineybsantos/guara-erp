# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
# Este programa é software livre: redistribua/modifique sob os termos da GNU GPL v3 ou posterior.
# Distribuído SEM NENHUMA GARANTIA. Veja o arquivo LICENSE.
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from apps.core import views as core_views
from apps.core import views_inicio
from apps.seguranca import views as seguranca_views

handler500 = "apps.core.views_auditoria.erro_500"

admin.site.site_header = "Guará ERP - Administração"
admin.site.site_title = "Guará ERP"
admin.site.index_title = "Administração do sistema"

urlpatterns = [
    path("", core_views.dashboard, name="dashboard"),
    path("avisos/", core_views.avisos, name="avisos"),
    path("primeiros-passos/", views_inicio.primeiros_passos, name="primeiros_passos"),
    path("empresa/trocar/", core_views.trocar_empresa, name="trocar_empresa"),
    path("login/", core_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("senha/", seguranca_views.TrocarSenhaView.as_view(), name="password_change"),
    path("banco-de-dados/", core_views.banco_dados, name="banco_dados"),
    path("banco-de-dados/backup/", core_views.banco_backup, name="banco_backup"),
    path("banco-de-dados/manutencao/", core_views.banco_manutencao, name="banco_manutencao"),
    path("cadastros/", include("apps.core.urls")),
    path("financeiro/", include("apps.financeiro.urls")),
    path("cobranca/", include("apps.cobranca.urls")),
    path("fiscal/", include("apps.fiscal.urls")),
    path("workflow/", include("apps.workflow.urls")),
    path("ged/", include("apps.ged.urls")),
    path("tarefas/", include("apps.tarefas.urls")),
    path("juridico/", include("apps.juridico.urls")),
    path("compras/", include("apps.compras.urls")),
    path("estoque/", include("apps.estoque.urls")),
    path("rh/", include("apps.rh.urls")),
    path("seguranca/", include("apps.seguranca.urls")),
    path("identidade/", include("apps.identidade.urls")),
    path("scim/v2/", include("apps.identidade.urls_scim")),
    path("relatorios/", include("apps.relatorios.urls")),
    path("assistente/", include("apps.assistente.urls")),
    path("bancos/", include("apps.bancario.urls")),
    path("contabilidade/", include("apps.contabilidade.urls")),
    path("servicedesk/", include("apps.servicedesk.urls")),
    path("sst/", include("apps.sst.urls")),
    path("crm/", include("apps.crm.urls")),
    path("projetos/", include("apps.projetos.urls")),
    path("manutencao/", include("apps.manutencao.urls")),
    path("qualidade/", include("apps.qualidade.urls")),
    path("producao/", include("apps.producao.urls")),
    path("vendas/", include("apps.vendas.urls")),
    # O login do Admin passa pela mesma tela (com bloqueio por tentativas e auditoria)
    path("admin/login/", RedirectView.as_view(url="/login/", query_string=True)),
    path("admin/", admin.site.urls),
]

# Nenhum arquivo enviado (MEDIA) é servido diretamente: GED e XML fiscal passam por views
# que verificam login, permissão e forçam download seguro.
