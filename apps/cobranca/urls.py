from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views, views_gateway

app_name = "cobranca"

urlpatterns = [
    path("gerar/<int:pk>/", views_gateway.gerar_pelo_gateway, name="gerar_cobranca"),
    path("cobranca/<int:pk>/atualizar/", views_gateway.atualizar_do_gateway, name="atualizar_do_gateway"),
    path("cobranca/<int:pk>/cancelar/", views_gateway.cancelar_cobranca, name="cancelar_cobranca"),
    path("webhook/asaas/", views_gateway.webhook_asaas, name="webhook_asaas"),
    path("webhook/efi/<str:segredo>/", views_gateway.webhook_efi, name="webhook_efi"),
    path("gateway/", views_gateway.gateways, name="gateways"),
    path("gateway/novo/", views_gateway.gateway_form, name="gateway_novo"),
    path("gateway/<int:pk>/", views_gateway.gateway_form, name="gateway_editar"),
    path("gateway/<int:pk>/testar/", views_gateway.gateway_testar, name="gateway_testar"),
    path("gateway/<int:pk>/novo-token/", views_gateway.gateway_novo_token, name="gateway_novo_token"),
    path("gateway/sincronizar/", views_gateway.gateway_sincronizar, name="gateway_sincronizar"),
    path("cobranca/<int:pk>/enviar/", views.enviar_cobranca, name="enviar_cobranca"),
    path("regua/executar/", views.executar_regua, name="executar_regua"),
] + crud_urls(telas.CobrancaCrud, telas.ReguaCobrancaCrud, telas.HistoricoCobrancaCrud)
