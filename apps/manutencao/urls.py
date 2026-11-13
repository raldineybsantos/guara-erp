from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "manutencao"

urlpatterns = crud_urls(telas.OrdemServicoCrud, telas.PlanoManutencaoCrud, telas.EquipamentoCrud) + [
    path("os/<int:pk>/iniciar/", views.iniciar, name="iniciar"),
    path("os/<int:pk>/concluir/", views.concluir, name="concluir"),
    path("os/<int:pk>/cancelar/", views.cancelar, name="cancelar"),
]
