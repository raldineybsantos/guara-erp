from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "vendas"

urlpatterns = crud_urls(telas.PedidoVendaCrud, telas.TabelaPrecoCrud, telas.RegraComissaoCrud, telas.ComissaoCrud) + [
    path("preco-item/", views.preco_item, name="preco_item"),
    path("pedido/<int:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("pedido/<int:pk>/faturar/", views.faturar, name="faturar"),
    path("pedido/<int:pk>/liberar-credito/", views.liberar_credito, name="liberar_credito"),
    path("comissao/<int:pk>/lancar/", views.lancar_comissao, name="lancar_comissao"),
    path("pedido/<int:pk>/cancelar/", views.cancelar, name="cancelar"),
]
