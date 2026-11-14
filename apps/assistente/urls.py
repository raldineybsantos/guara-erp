from django.urls import path

from . import views

app_name = "assistente"

urlpatterns = [
    path("", views.conversa, name="conversa"),
    path("pedido/<int:pk>/descartar/", views.descartar, name="descartar"),
    path("vincular/", views.vincular, name="vincular"),
    path("configuracao/", views.configuracao, name="configuracao"),
    path("telegram/webhook/", views.telegram_webhook, name="telegram_webhook"),
]
