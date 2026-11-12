from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "ged"

urlpatterns = [
    path("versao/<int:pk>/download/", views.download, name="download"),
    path("documento/<int:pk>/nova-versao/", views.nova_versao, name="nova_versao"),
] + crud_urls(telas.DocumentoCrud, telas.PastaCrud)
