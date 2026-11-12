from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "fiscal"

urlpatterns = [
    path("importar-xml/", views.importar_xml, name="importar_xml"),
    path("apuracao/", views.apuracao, name="apuracao"),
    path("nota/<int:pk>/emitir/", views.emitir_nota, name="emitir_nota"),
    path("nota/<int:pk>/cancelar/", views.cancelar_nota, name="cancelar_nota"),
    path("nota/<int:pk>/gerar-titulo/", views.gerar_titulo_nota, name="gerar_titulo_nota"),
] + crud_urls(telas.NotaFiscalCrud, telas.ProdutoCrud)
