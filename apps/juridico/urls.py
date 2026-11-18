from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "juridico"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("contrato/<int:pk>/enviar-aprovacao/", views.enviar_aprovacao, name="enviar_aprovacao"),
    path("contrato/<int:pk>/conferir-assinaturas/", views.conferir_assinaturas, name="conferir_assinaturas"),
    path("contrato/<int:pk>/assinatura-manual/", views.assinatura_manual, name="assinatura_manual"),
    path("contrato/<int:pk>/renovar/", views.renovar, name="renovar"),
    path("contrato/<int:pk>/encerrar/", views.encerrar, name="encerrar"),
    path("contrato/<int:pk>/reajustar/", views.reajustar, name="reajustar"),
    path("medicao/<int:pk>/aprovar/", views.medicao_aprovar, name="medicao_aprovar"),
    path("medicao/<int:pk>/faturar/", views.medicao_faturar, name="medicao_faturar"),
    path("medicao/<int:pk>/cancelar/", views.medicao_cancelar, name="medicao_cancelar"),
    path("caucao/<int:pk>/devolver/", views.caucao_devolver, name="caucao_devolver"),
    path("caucao/<int:pk>/executar/", views.caucao_executar, name="caucao_executar"),
    path("processo/<int:pk>/andamento/", views.processo_andamento, name="processo_andamento"),
    path("processo/<int:pk>/prazo/", views.processo_prazo, name="processo_prazo"),
    path("processo/<int:pk>/envolvido/", views.processo_envolvido, name="processo_envolvido"),
    path("processo/<int:pk>/risco/", views.processo_risco, name="processo_risco"),
    path("processo/<int:pk>/encerrar-processo/", views.processo_encerrar, name="processo_encerrar"),
    path("processo/<int:pk>/suspender/", views.processo_suspender, name="processo_suspender"),
    path("processo/<int:pk>/retomar/", views.processo_retomar, name="processo_retomar"),
    path("prazo/<int:pk>/cumprir/", views.prazo_acao, {"acao": "cumprir"}, name="prazo_cumprir"),
    path("prazo/<int:pk>/cancelar/", views.prazo_acao, {"acao": "cancelar"}, name="prazo_cancelar"),
    path("prazo/<int:pk>/perdido/", views.prazo_acao, {"acao": "marcar_perdido"}, name="prazo_perdido"),
    path("contencioso/", views.contencioso, name="contencioso"),
    path("paralisacao/<int:pk>/retomar/", views.paralisacao_retomar, name="paralisacao_retomar"),
] + crud_urls(telas.ContratoCrud, telas.AditivoCrud, telas.ValorIndiceCrud, telas.MedicaoCrud, telas.CaucaoCrud,
              telas.ParalisacaoCrud, telas.ProcessoCrud, telas.PrazoCrud)
