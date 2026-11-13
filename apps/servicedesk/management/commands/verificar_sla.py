"""Avisos de SLA do Service Desk. O agendador roda a cada 15 minutos; no Windows, agende este comando."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Avisa SLA em risco/vencido e fecha chamados resolvidos não confirmados."

    def handle(self, *args, **options):
        from apps.servicedesk.models import verificar_slas

        avisos, fechados = verificar_slas()
        self.stdout.write(f"Service Desk: {avisos} aviso(s) de SLA, {fechados} chamado(s) fechado(s).")
