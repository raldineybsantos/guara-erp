"""Agendador simples: executa a régua de cobrança uma vez por dia no horário definido.

Uso: python manage.py agendador --hora 08:00
(No Docker roda no serviço `agendador`; no Windows pode-se usar o Agendador de Tarefas com executar_regua.)
"""
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.cobranca.models import ReguaCobranca


class Command(BaseCommand):
    help = "Executa a régua de cobrança diariamente no horário informado (fuso do sistema: TIME_ZONE)."

    def add_arguments(self, parser):
        parser.add_argument("--hora", default="08:00", help="Horário HH:MM (padrão 08:00)")

    def handle(self, *args, hora, **options):
        alvo = hora.strip()
        ultimo_dia = None
        self.stdout.write(f"Agendador ativo: régua de cobrança diariamente às {alvo}.")
        while True:
            agora = timezone.localtime()
            if agora.strftime("%H:%M") >= alvo and ultimo_dia != agora.date():
                try:
                    n = ReguaCobranca.executar()
                    self.stdout.write(f"{agora:%d/%m/%Y %H:%M} régua executada: {n} envio(s).")
                except Exception as exc:  # mantém o agendador vivo
                    self.stderr.write(f"Erro na régua: {exc}")
                ultimo_dia = agora.date()
            time.sleep(30)
