"""Rotinas diárias dos módulos operacionais: OS preventivas e alertas de ASO/treinamentos vencendo.

Roda pelo serviço `agendador` (Docker) ou pelo Agendador de Tarefas (Windows, regua_windows.bat)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Abre OS preventivas que vencem e cria alertas de SST (ASO e reciclagem de NR)."

    def handle(self, *args, **options):
        from apps.manutencao.models import gerar_preventivas
        from apps.sst.models import alertar_vencimentos

        os_ = gerar_preventivas()
        sst = alertar_vencimentos()
        self.stdout.write(f"Manutenção: {os_} OS preventiva(s) aberta(s). SST: {sst} alerta(s).")
