from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "producao"

urlpatterns = crud_urls(telas.OrdemProducaoCrud, telas.EstruturaCrud, telas.OperacaoRoteiroCrud) + [
    path("ordem/<int:pk>/liberar/", views.liberar, name="liberar"),
    path("ordem/<int:pk>/apontar/", views.apontar, name="apontar"),
    path("ordem/<int:pk>/comprar/", views.gerar_sc, name="gerar_sc"),
    path("ordem/<int:pk>/encerrar/", views.encerrar, name="encerrar"),
    path("ordem/<int:pk>/cancelar/", views.cancelar, name="cancelar"),
]
