from django.urls import path

from apps.core.crud import crud_urls

from . import telas, views

app_name = "workflow"

urlpatterns = [
    path("tarefas/", views.minhas_tarefas, name="minhas_tarefas"),
    path("solicitacao/nova/", views.nova_solicitacao, name="nova_solicitacao"),
    path("solicitacao/<int:pk>/agir/", views.agir, name="agir"),
    path("aprovacao-pagamento/<int:pk>/", views.iniciar_aprovacao_pagar, name="iniciar_aprovacao_pagar"),
] + crud_urls(telas.ProcessoCrud, telas.InstanciaCrud)
