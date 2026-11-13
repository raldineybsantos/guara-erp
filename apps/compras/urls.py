from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "compras"

urlpatterns = crud_urls(telas.SolicitacaoCompraCrud, telas.CotacaoCrud, telas.PedidoCompraCrud,
                        telas.RecebimentoCompraCrud, telas.AlcadaCompraCrud) + [
    path("solicitacao/<int:pk>/cotacao/", views.gerar_cotacao, name="gerar_cotacao"),
    path("cotacao/<int:pk>/mapa/", views.mapa_cotacao, name="mapa_cotacao"),
    path("pedido/<int:pk>/enviar/", views.enviar_pedido, name="enviar_pedido"),
    path("pedido/<int:pk>/receber/", views.receber_pedido, name="receber_pedido"),
    path("pedido/<int:pk>/cancelar/", views.cancelar_pedido, name="cancelar_pedido"),
    path("pedido/<int:pk>/pdf/", views.pdf_pedido, name="pdf_pedido"),
    path("recebimento/<int:pk>/conferir/", views.conferir_recebimento, name="conferir_recebimento"),
]
