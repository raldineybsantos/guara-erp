from django.urls import path

from .crud import crud_urls
from . import telas, views_auditoria, views_parceiros

app_name = "core"

urlpatterns = crud_urls(telas.EmpresaCrud, telas.DepartamentoCrud, telas.CargoCrud, telas.ColaboradorCrud,
                        telas.FornecedorCrud, telas.ClienteCrud, telas.ParceiroNegocioCrud,
                        telas.LogAuditoriaCrud, telas.LogErroCrud) + [
    path("auditoria/integridade/", views_auditoria.integridade, name="auditoria_integridade"),
    path("auditoria/historico/<str:app>/<str:modelo>/<str:pk>/", views_auditoria.historico, name="historico"),
    path("logerro/<int:pk>/resolver/", views_auditoria.erro_resolver, name="erro_resolver"),
    path("logerro/<int:pk>/ignorar/", views_auditoria.erro_ignorar, name="erro_ignorar"),
    path("logerro/<int:pk>/analisar/", views_auditoria.erro_analisar, name="erro_analisar"),
    path("consulta-cnpj/", views_parceiros.consulta_cnpj, name="consulta_cnpj"),
    path("consulta-cep/", views_parceiros.consulta_cep, name="consulta_cep"),
    path("fornecedores/<int:pk>/tornar-cliente/", views_parceiros.tornar_cliente, name="tornar_cliente"),
    path("clientes/<int:pk>/tornar-fornecedor/", views_parceiros.tornar_fornecedor, name="tornar_fornecedor"),
]
