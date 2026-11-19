from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "bancario"

urlpatterns = crud_urls(telas.ExtratoCrud, telas.BorderoCrud, telas.RemessaCobrancaCrud) + [
    path("extrato/importar/", views.importar_extrato, name="importar_extrato"),
    path("linha/<int:pk>/resolver/", views.resolver_linha, name="resolver_linha"),
    path("bordero/<int:pk>/titulos/", views.bordero_titulos, name="bordero_titulos"),
    path("bordero/<int:pk>/remessa/", views.bordero_remessa, name="bordero_remessa"),
    path("bordero/<int:pk>/cancelar/", views.bordero_cancelar, name="bordero_cancelar"),
    path("retorno/", views.importar_retorno, name="importar_retorno"),
    path("cobranca/<int:pk>/titulos/", views.remessa_titulos, name="remessa_titulos"),
    path("cobranca/<int:pk>/arquivo/", views.remessa_arquivo, name="remessa_arquivo"),
    path("cobranca/<int:pk>/cancelar/", views.remessa_cancelar, name="remessa_cancelar"),
    path("cobranca/retorno/", views.importar_retorno_cobranca, name="importar_retorno_cobranca"),
]
