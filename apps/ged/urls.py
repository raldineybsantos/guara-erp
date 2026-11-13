from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views, views_assinatura

app_name = "ged"

urlpatterns = [
    path("versao/<int:pk>/download/", views.download, name="download"),
    path("documento/<int:pk>/nova-versao/", views.nova_versao, name="nova_versao"),
    path("versao/<int:pk>/assinar/", views_assinatura.assinar, name="assinar"),
    path("versao/<int:pk>/verificar/", views_assinatura.verificar, name="verificar"),
    path("assinatura/<int:pk>/p7s/", views_assinatura.baixar_p7s, name="baixar_p7s"),
] + crud_urls(telas.DocumentoCrud, telas.PastaCrud)
