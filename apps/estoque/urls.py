from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "estoque"

urlpatterns = crud_urls(telas.SaldoCrud, telas.MovimentoCrud, telas.InventarioCrud, telas.ArmazemCrud) + [
    path("movimentar/", views.movimentar, name="movimentar"),
    path("reposicao/", views.reposicao, name="reposicao"),
    path("inventario/<int:pk>/encerrar/", views.encerrar_inventario, name="encerrar_inventario"),
]
