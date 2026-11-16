# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.seguranca import backup


class Command(BaseCommand):
    help = "Confere um backup (decifra, valida o manifesto e lê o banco com pg_restore --list) sem restaurar nada."

    def add_arguments(self, parser):
        parser.add_argument("arquivo")
        parser.add_argument("--senha", default=None, help="Padrão: BACKUP_SENHA do ambiente.")

    def handle(self, *args, arquivo, senha, **options):
        try:
            m = backup.verificar(Path(arquivo), senha)
        except backup.BackupErro as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"OK — gerado em {m['gerado_em']}, banco '{m['banco']}', {m['migracoes']} migrações, "
                          f"{m['arquivos_media']} arquivo(s).")
