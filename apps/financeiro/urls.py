from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "financeiro"

urlpatterns = [
    path("fluxo-caixa/", views.fluxo_caixa, name="fluxo_caixa"),
    path("transferencia/", views.transferencia, name="transferencia"),
    path("pagar/<int:pk>/baixar/", views.baixar, {"tipo": "pagar"}, name="titulo_baixar_pagar"),
    path("receber/<int:pk>/baixar/", views.baixar, {"tipo": "receber"}, name="titulo_baixar_receber"),
    path("pagar/<int:pk>/estornar/", views.estornar, {"tipo": "pagar"}, name="titulo_estornar_pagar"),
    path("receber/<int:pk>/estornar/", views.estornar, {"tipo": "receber"}, name="titulo_estornar_receber"),
    path("pagar/<int:pk>/retencoes/", views.calcular_retencoes, name="titulo_retencoes"),
    path("pagar/<int:pk>/ratear/", views.ratear, {"tipo": "pagar"}, name="titulo_ratear_pagar"),
    path("receber/<int:pk>/ratear/", views.ratear, {"tipo": "receber"}, name="titulo_ratear_receber"),
    path("guias/<int:pk>/recolher/", views.recolher_guia, name="guia_recolher"),
    path("guias/<int:pk>/estornar/", views.estornar_guia, name="guia_estornar"),
    path("relatorios/centro-custo/", views.relatorio_centro_custo, name="relatorio_centro_custo"),
    path("pagar/<int:pk>/cancelar/", views.cancelar, {"tipo": "pagar"}, name="titulo_cancelar_pagar"),
    path("receber/<int:pk>/cancelar/", views.cancelar, {"tipo": "receber"}, name="titulo_cancelar_receber"),
] + crud_urls(telas.ContaPagarCrud, telas.ContaReceberCrud, telas.MovimentacaoCrud, telas.ContaBancariaCrud,
              telas.PlanoContaCrud, telas.CentroCustoCrud, telas.RegraRetencaoCrud, telas.GuiaTributoCrud)
