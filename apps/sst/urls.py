from apps.core.crud import crud_urls

from . import telas

app_name = "sst"

urlpatterns = crud_urls(telas.IncidenteCrud, telas.AvaliacaoRiscoCrud, telas.EntregaEPICrud,
                        telas.ExameOcupacionalCrud, telas.TreinamentoSSTCrud, telas.MembroCIPACrud,
                        telas.ReuniaoCIPACrud, telas.LicencaAmbientalCrud, telas.ResiduoGeradoCrud)
