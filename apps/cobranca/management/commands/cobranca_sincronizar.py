# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.core.management.base import BaseCommand

from apps.cobranca.gateway import servico


class Command(BaseCommand):
    help = "Concilia com o gateway: baixa pagamentos cujo aviso se perdeu e cancela no gateway o que foi cancelado aqui."

    def handle(self, *args, **options):
        r = servico.sincronizar()
        self.stdout.write(f"Gateway: {r['baixadas']} baixa(s), {r['canceladas']} cancelamento(s), {r['erros']} erro(s).")
