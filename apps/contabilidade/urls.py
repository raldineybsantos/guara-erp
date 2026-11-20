from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "contabilidade"

urlpatterns = crud_urls(telas.LancamentoCrud, telas.ContaContabilCrud, telas.RegraContabilCrud, telas.BemCrud, telas.ContratoContabilCrud,
                          telas.OrcamentoCrud) + [
    path("lancar/", views.lancar_manual, name="lancar_manual"),
    path("lancamento/<int:pk>/estornar/", views.estornar, name="estornar"),
    path("balancete/", views.balancete, name="balancete"),
    path("razao/", views.razao, name="razao"),
    path("dre/", views.dre, name="dre"),
    path("balanco/", views.balanco, name="balanco"),
    path("bens/depreciar/", views.depreciar, name="depreciar"),
    path("contratos/apropriar/", views.apropriar_contratos, name="apropriar_contratos"),
    path("bens/<int:pk>/baixar/", views.bem_baixar, name="bem_baixar"),
    path("orcamento/<int:pk>/orcado-realizado/", views.orcado_realizado, name="orcado_realizado"),
    path("periodos/", views.periodos, name="periodos"),
    path("encerramento/", views.encerrar_exercicio, name="encerrar_exercicio"),
    path("ecd/", views.gerar_ecd, name="gerar_ecd"),
]
