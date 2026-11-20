from django.urls import path
from django.contrib.auth.decorators import login_required
from django.views.generic import RedirectView

from . import views

app_name = "relatorios"

urlpatterns = [
    path("", login_required(RedirectView.as_view(pattern_name="relatorios:executivo", query_string=True))),
    path("executivo/", views.executivo, name="executivo"),
    path("financeiro/", views.financeiro, name="financeiro"),
    path("comercial/", views.comercial, name="comercial"),
    path("suprimentos/", views.suprimentos, name="suprimentos"),
    path("operacao/", views.operacao, name="operacao"),
    path("service-desk/", views.servicedesk, name="servicedesk"),
]
