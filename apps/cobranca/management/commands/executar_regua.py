from django.core.management.base import BaseCommand

from apps.cobranca.models import ReguaCobranca


class Command(BaseCommand):
    help = "Envia as cobranças previstas pela régua para hoje (agende diariamente via cron / Agendador de Tarefas)."

    def handle(self, *args, **options):
        n = ReguaCobranca.executar()
        self.stdout.write(self.style.SUCCESS(f"{n} cobrança(s) enviada(s)."))
