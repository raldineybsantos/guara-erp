from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "servicedesk"

urlpatterns = crud_urls(telas.ChamadoCrud, telas.AreaCrud, telas.ServicoCrud, telas.ArtigoCrud,
                        telas.MudancaCrud) + [
    path("abrir/", views.catalogo, name="catalogo"),
    path("abrir/assistente/", views.abrir_com_ia, name="abrir_com_ia"),
    path("chamado/<int:pk>/ia/", views.ia_resposta, name="ia_resposta"),
    path("fila/", views.fila, name="fila"),
    path("assistente/", views.assistente, name="assistente"),
    path("solucao/<int:pk>/", views.artigo_ler, name="artigo_ler"),
    path("chamado/<int:pk>/mensagens/", views.mensagens, name="mensagens"),
    path("chamado/<int:pk>/assumir/", views.assumir, name="assumir"),
    path("chamado/<int:pk>/chamar-usuario/", views.chamar_usuario, name="chamar_usuario"),
    path("chamado/<int:pk>/resolver/", views.resolver, name="resolver"),
    path("chamado/<int:pk>/fechar/", views.fechar, name="fechar"),
    path("chamado/<int:pk>/reabrir/", views.reabrir, name="reabrir"),
    path("chamado/<int:pk>/cancelar/", views.cancelar, name="cancelar"),
    path("chamado/<int:pk>/transferir/", views.transferir, name="transferir"),
    path("mudanca/<int:pk>/enviar/", views.mudanca_enviar, name="mudanca_enviar"),
    path("mudanca/<int:pk>/executar/", views.mudanca_executar, name="mudanca_executar"),
    path("mudanca/<int:pk>/concluir/", views.mudanca_concluir, name="mudanca_concluir"),
]
