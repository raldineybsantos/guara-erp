from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "tarefas"

urlpatterns = [
    path("", views.quadro, name="quadro"),
    path("mover/", views.mover, name="mover"),
    path("atividade/<int:pk>/comentar/", views.comentar, name="comentar"),
] + crud_urls(telas.AtividadeCrud)
