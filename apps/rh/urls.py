from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "rh"

urlpatterns = [
    path("ponto/bater/", views.bater_ponto, name="bater_ponto"),
    path("ponto/espelho/", views.espelho, name="meu_espelho"),
    path("ponto/espelho/<int:pk>/", views.espelho, name="espelho"),
    path("ponto/banco-de-horas/", views.banco_horas, name="banco_horas"),
    path("ferias/solicitar/", views.solicitar_ferias, name="solicitar_ferias"),
    path("ferias/minhas/", views.minhas_ferias, name="minhas_ferias"),
    path("ferias/gerar-periodos/", views.gerar_periodos, name="gerar_periodos"),
    path("ferias/<int:pk>/aprovar/", views.ferias_aprovar, name="ferias_aprovar"),
    path("ferias/<int:pk>/recusar/", views.ferias_recusar, name="ferias_recusar"),
    path("ferias/<int:pk>/gozada/", views.ferias_gozada, name="ferias_gozada"),
    path("folha/<int:pk>/calcular/", views.folha_calcular, name="folha_calcular"),
    path("folha/<int:pk>/importar-ponto/", views.folha_importar_ponto, name="folha_importar_ponto"),
    path("folha/<int:pk>/fechar/", views.folha_fechar, name="folha_fechar"),
    path("folha/<int:pk>/reabrir/", views.folha_reabrir, name="folha_reabrir"),
    path("holerite/<int:pk>/", views.holerite_ver, name="holerite_ver"),
    path("holerites/meus/", views.meus_holerites, name="meus_holerites"),
    path("esocial/gerar/", views.esocial_gerar, name="esocial_gerar"),
    path("esocial/lote/", views.esocial_montar_lote, name="esocial_montar_lote"),
    path("esocial/folha/<int:pk>/", views.esocial_da_folha, name="esocial_da_folha"),
    path("esocial/rescisao/<int:pk>/", views.esocial_da_rescisao, name="esocial_da_rescisao"),
    path("esocial/evento/<int:pk>/assinar/", views.esocial_assinar, name="esocial_assinar"),
    path("esocial/evento/<int:pk>/xml/", views.esocial_baixar, name="esocial_baixar"),
    path("esocial/evento/<int:pk>/lote/", views.esocial_lote, name="esocial_lote"),
    path("esocial/evento/<int:pk>/retorno/", views.esocial_recibo, name="esocial_recibo"),
    path("rescisao/<int:pk>/calcular/", views.rescisao_calcular, name="rescisao_calcular"),
    path("rescisao/<int:pk>/efetivar/", views.rescisao_efetivar, name="rescisao_efetivar"),
]
urlpatterns += crud_urls(
    telas.ParametroFolhaCrud, telas.FaixaIRRFCrud, telas.JornadaCrud, telas.DadosTrabalhistasCrud, telas.DependenteCrud,
    telas.BeneficioCrud, telas.BeneficioColaboradorCrud, telas.RubricaCrud, telas.MarcacaoCrud, telas.AbonoPontoCrud,
    telas.AjusteBancoHorasCrud, telas.FeriadoCrud, telas.PeriodoAquisitivoCrud, telas.SolicitacaoFeriasCrud,
    telas.ConfiguracaoESocialCrud, telas.LancamentoFolhaCrud, telas.FolhaCrud, telas.RescisaoCrud, telas.EventoESocialCrud)
