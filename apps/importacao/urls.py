from django.urls import path

from . import views

app_name = "importacao"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nova/", views.nova, name="nova"),
    path("nova-banco/", views.nova_banco, name="nova_banco"),
    path("<int:pk>/tabela/", views.tabela, name="tabela"),
    path("<int:pk>/mapear/", views.mapear, name="mapear"),
    path("<int:pk>/", views.detalhe, name="detalhe"),
    path("<int:pk>/importar/", views.importar, name="importar"),
    path("<int:pk>/desfazer/", views.desfazer, name="desfazer"),
    path("<int:pk>/excluir/", views.excluir, name="excluir"),
    path("<int:pk>/erros.csv", views.erros_csv, name="erros_csv"),
]
