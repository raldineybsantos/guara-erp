from django.urls import path

from .crud import crud_urls
from . import telas, views_parceiros

app_name = "core"

urlpatterns = crud_urls(telas.EmpresaCrud, telas.DepartamentoCrud, telas.CargoCrud, telas.ColaboradorCrud,
                        telas.FornecedorCrud, telas.ClienteCrud, telas.ParceiroNegocioCrud,
                        telas.LogAuditoriaCrud) + [
    path("fornecedores/<int:pk>/tornar-cliente/", views_parceiros.tornar_cliente, name="tornar_cliente"),
    path("clientes/<int:pk>/tornar-fornecedor/", views_parceiros.tornar_fornecedor, name="tornar_fornecedor"),
]
