# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.core.management.base import BaseCommand, CommandError

from apps.seguranca import antivirus, varredura


class Command(BaseCommand):
    help = "Verifica com o ClamAV os arquivos do GED que ainda não foram examinados (use --tudo para reexaminar todos)."

    def add_arguments(self, parser):
        parser.add_argument("--tudo", action="store_true", help="Reexamina também os já marcados como sem ameaças.")

    def handle(self, *args, tudo, **options):
        if not antivirus.ativo():
            raise CommandError("ANTIVIRUS_ATIVO está desligado.")
        if not antivirus.ping():
            raise CommandError("O ClamAV não respondeu (confira CLAMAV_HOST e CLAMAV_PORT).")
        situ = ("nao_verificado", "erro", "limpo", "infectado") if tudo else ("nao_verificado", "erro")
        r = varredura.varrer(situ)
        self.stdout.write(f"Sem ameaças: {r['limpo']} · AMEAÇAS: {r['infectado']} · não verificados: {r['erro'] + r['ausente']}")
