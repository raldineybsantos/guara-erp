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
    path("paralisacao/<int:pk>/retomar/", views.paralisacao_retomar, name="paralisacao_retomar"),
] + crud_urls(telas.ContratoCrud, telas.AditivoCrud, telas.ValorIndiceCrud, telas.MedicaoCrud, telas.CaucaoCrud,
              telas.ParalisacaoCrud)
