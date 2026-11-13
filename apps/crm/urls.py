from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "crm"

urlpatterns = crud_urls(telas.OportunidadeCrud) + [
    path("funil/", views.funil, name="funil"),
    path("oportunidade/<int:pk>/ganhar/", views.ganhar, name="ganhar"),
    path("oportunidade/<int:pk>/perder/", views.perder, name="perder"),
    path("oportunidade/<int:pk>/interagir/", views.interagir, name="interagir"),
]
