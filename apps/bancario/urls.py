from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "bancario"

urlpatterns = crud_urls(telas.ExtratoCrud, telas.BorderoCrud) + [
    path("extrato/importar/", views.importar_extrato, name="importar_extrato"),
    path("linha/<int:pk>/resolver/", views.resolver_linha, name="resolver_linha"),
    path("bordero/<int:pk>/titulos/", views.bordero_titulos, name="bordero_titulos"),
    path("bordero/<int:pk>/remessa/", views.bordero_remessa, name="bordero_remessa"),
    path("bordero/<int:pk>/cancelar/", views.bordero_cancelar, name="bordero_cancelar"),
    path("retorno/", views.importar_retorno, name="importar_retorno"),
]
