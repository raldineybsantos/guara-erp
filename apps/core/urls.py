from django.urls import path

from .crud import crud_urls
from . import telas

app_name = "core"

urlpatterns = crud_urls(telas.EmpresaCrud, telas.DepartamentoCrud, telas.CargoCrud, telas.ColaboradorCrud,
                        telas.FornecedorCrud, telas.ClienteCrud, telas.LogAuditoriaCrud)
