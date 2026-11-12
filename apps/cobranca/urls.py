from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "cobranca"

urlpatterns = [
    path("gerar/<int:pk>/", views.gerar_cobranca, name="gerar_cobranca"),
    path("cobranca/<int:pk>/enviar/", views.enviar_cobranca, name="enviar_cobranca"),
    path("regua/executar/", views.executar_regua, name="executar_regua"),
] + crud_urls(telas.CobrancaCrud, telas.ReguaCobrancaCrud, telas.HistoricoCobrancaCrud)
