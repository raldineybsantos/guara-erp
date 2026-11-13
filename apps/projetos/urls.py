from apps.core.crud import crud_urls

from . import telas

app_name = "projetos"

urlpatterns = crud_urls(telas.ProjetoCrud, telas.ApontamentoHorasCrud)
