# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.core.management.base import BaseCommand, CommandError

from apps.seguranca import backup


class Command(BaseCommand):
    help = "Faz o backup (banco + arquivos), cifra, confere, copia para fora do servidor e aplica a retenção."

    def handle(self, *args, **options):
        reg = backup.executar("agendado")
        if reg.situacao != "ok":
            raise CommandError(f"Backup falhou: {reg.erro}")
        self.stdout.write(f"Backup {reg.arquivo} ({reg.tamanho / 1048576:.1f} MiB) — verificado; "
                          f"cópia externa: {reg.copia_externa or 'não configurada'}; {reg.removidos} antigo(s) removido(s).")
        if reg.erro:
            self.stderr.write(reg.erro)
