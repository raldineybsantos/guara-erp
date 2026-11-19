from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views, views_desempenho, views_treinamento

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
    path("treinamentos/turma/<int:pk>/inscrever/", views_treinamento.turma_inscrever, name="turma_inscrever"),
    path("treinamentos/turma/<int:pk>/iniciar/", views_treinamento.turma_iniciar, name="turma_iniciar"),
    path("treinamentos/turma/<int:pk>/resultados/", views_treinamento.turma_resultados, name="turma_resultados"),
    path("treinamentos/turma/<int:pk>/concluir/", views_treinamento.turma_concluir, name="turma_concluir"),
    path("treinamentos/turma/<int:pk>/cancelar/", views_treinamento.turma_cancelar, name="turma_cancelar"),
    path("treinamentos/pendencias/", views_treinamento.pendencias_treinamento, name="pendencias_treinamento"),
    path("treinamentos/meus/", views_treinamento.meus_treinamentos, name="meus_treinamentos"),
    path("desempenho/ciclo/<int:pk>/abrir/", views_desempenho.ciclo_abrir, name="ciclo_abrir"),
    path("desempenho/ciclo/<int:pk>/calibrar/", views_desempenho.ciclo_calibrar, name="ciclo_calibrar"),
    path("desempenho/ciclo/<int:pk>/encerrar/", views_desempenho.ciclo_encerrar, name="ciclo_encerrar"),
    path("desempenho/avaliacao/<int:pk>/auto/", views_desempenho.avaliacao_responder_auto, name="avaliacao_responder_auto"),
    path("desempenho/avaliacao/<int:pk>/gestor/", views_desempenho.avaliacao_responder_gestor, name="avaliacao_responder_gestor"),
    path("desempenho/avaliacao/<int:pk>/pdi/", views_desempenho.avaliacao_pdi, name="avaliacao_pdi"),
    path("desempenho/pdi/<int:pk>/concluir/", views_desempenho.pdi_concluir, name="pdi_concluir"),
    path("desempenho/pdi/<int:pk>/cancelar/", views_desempenho.pdi_cancelar, name="pdi_cancelar"),
    path("desempenho/minhas/", views_desempenho.minhas_avaliacoes, name="minhas_avaliacoes"),
    path("rescisao/<int:pk>/calcular/", views.rescisao_calcular, name="rescisao_calcular"),
    path("rescisao/<int:pk>/efetivar/", views.rescisao_efetivar, name="rescisao_efetivar"),
]
urlpatterns += crud_urls(
    telas.ParametroFolhaCrud, telas.FaixaIRRFCrud, telas.JornadaCrud, telas.DadosTrabalhistasCrud, telas.DependenteCrud,
    telas.BeneficioCrud, telas.BeneficioColaboradorCrud, telas.RubricaCrud, telas.MarcacaoCrud, telas.AbonoPontoCrud,
    telas.AjusteBancoHorasCrud, telas.FeriadoCrud, telas.PeriodoAquisitivoCrud, telas.SolicitacaoFeriasCrud,
    telas.ConfiguracaoESocialCrud, telas.LancamentoFolhaCrud, telas.FolhaCrud, telas.RescisaoCrud, telas.EventoESocialCrud,
    telas.CursoCrud, telas.TurmaCrud, telas.ParticipacaoCrud,
    telas.CompetenciaCrud, telas.ModeloAvaliacaoCrud, telas.ItemModeloCrud, telas.CicloAvaliacaoCrud, telas.AvaliacaoCrud,
    telas.AcaoDesenvolvimentoCrud)
