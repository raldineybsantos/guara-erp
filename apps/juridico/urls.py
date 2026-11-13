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
] + crud_urls(telas.ContratoCrud, telas.AditivoCrud)
