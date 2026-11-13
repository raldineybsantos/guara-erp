from django.core.management.base import BaseCommand

from apps.juridico.models import processar_vencimentos


class Command(BaseCommand):
    help = "Cria alertas no quadro de tarefas para contratos a vencer e renova os de renovação automática."

    def handle(self, *args, **options):
        alertas, renovados = processar_vencimentos()
        self.stdout.write(self.style.SUCCESS(f"{alertas} alerta(s) criado(s), {renovados} contrato(s) renovado(s)."))
