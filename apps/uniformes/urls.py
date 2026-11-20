from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "uniformes"

urlpatterns = [
    path("catalogo/", views.catalogo, name="catalogo"),
    path("imagem/<str:tipo>/<int:pk>/", views.imagem, name="imagem"),
    path("entregar/", views.entregar, name="entregar_avulso"),
    path("conjunto/<int:pk>/entregar/", views.entregar, name="entregar"),
    path("entrega/<int:pk>/termo/", views.entrega_termo, name="entrega_termo"),
    path("entrega/<int:pk>/assinada/", views.entrega_assinada, name="entrega_assinada"),
    path("entrega/<int:pk>/estornar/", views.entrega_estornar, name="entrega_estornar"),
] + crud_urls(telas.ModeloUniformeCrud, telas.ConjuntoUniformeCrud, telas.TamanhoColaboradorCrud, telas.EntregaUniformeCrud)
