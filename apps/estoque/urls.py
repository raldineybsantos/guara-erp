from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "estoque"

urlpatterns = crud_urls(telas.SaldoCrud, telas.MovimentoCrud, telas.InventarioCrud, telas.ArmazemCrud, telas.EnderecoCrud, telas.LoteCrud,
                                 telas.SaldoLoteCrud) + [
    path("movimentar/", views.movimentar, name="movimentar"),
    path("lotes-a-vencer/", views.lotes_a_vencer, name="lotes_a_vencer"),
    path("enderecar/", views.enderecar, name="enderecar"),
    path("reposicao/", views.reposicao, name="reposicao"),
    path("inventario/<int:pk>/encerrar/", views.encerrar_inventario, name="encerrar_inventario"),
]
