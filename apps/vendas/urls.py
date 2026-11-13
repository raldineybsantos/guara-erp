from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "vendas"

urlpatterns = crud_urls(telas.PedidoVendaCrud) + [
    path("pedido/<int:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("pedido/<int:pk>/faturar/", views.faturar, name="faturar"),
    path("pedido/<int:pk>/liberar-credito/", views.liberar_credito, name="liberar_credito"),
    path("pedido/<int:pk>/cancelar/", views.cancelar, name="cancelar"),
]
