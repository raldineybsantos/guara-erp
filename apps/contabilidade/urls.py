from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "contabilidade"

urlpatterns = crud_urls(telas.LancamentoCrud, telas.ContaContabilCrud, telas.RegraContabilCrud) + [
    path("lancar/", views.lancar_manual, name="lancar_manual"),
    path("lancamento/<int:pk>/estornar/", views.estornar, name="estornar"),
    path("balancete/", views.balancete, name="balancete"),
    path("razao/", views.razao, name="razao"),
    path("dre/", views.dre, name="dre"),
    path("balanco/", views.balanco, name="balanco"),
    path("periodos/", views.periodos, name="periodos"),
]
