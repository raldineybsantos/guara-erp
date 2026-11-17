"""Lança a depreciação mensal do ativo fixo. Por padrão, até o mês anterior; pode ser repetido sem duplicar."""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.contabilidade import patrimonio


class Command(BaseCommand):
    help = "Deprecia os bens do ativo fixo até o mês informado (padrão: mês anterior)."

    def add_arguments(self, parser):
        parser.add_argument("--ate", help="Competência AAAA-MM (padrão: mês anterior).")

    def handle(self, *args, **opts):
        if opts["ate"]:
            try:
                ano, mes = (int(x) for x in opts["ate"].split("-"))
                comp = datetime.date(ano, mes, 1)
            except ValueError:
                raise CommandError("Use --ate AAAA-MM.")
        else:
            comp = timezone.localdate().replace(day=1) - datetime.timedelta(days=1)
        n = patrimonio.depreciar_ate(comp)
        self.stdout.write(f"{n} depreciação(ões) lançada(s) até {comp:%m/%Y}.")
