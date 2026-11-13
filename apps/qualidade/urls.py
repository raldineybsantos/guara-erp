from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "qualidade"

urlpatterns = crud_urls(telas.InspecaoCrud, telas.NaoConformidadeCrud) + [
    path("inspecao/<int:pk>/resultado/", views.concluir_inspecao, name="concluir_inspecao"),
]
