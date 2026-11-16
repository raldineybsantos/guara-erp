# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.seguranca import backup


class Command(BaseCommand):
    help = ("RESTAURA um backup: apaga e recria as tabelas do banco com o conteúdo do arquivo e devolve os arquivos do GED. "
            "Operação destrutiva; pare o ERP antes.")

    def add_arguments(self, parser):
        parser.add_argument("arquivo")
        parser.add_argument("--senha", default=None, help="Padrão: BACKUP_SENHA do ambiente.")
        parser.add_argument("--banco", default=None, help="Banco de destino (padrão: o banco configurado).")
        parser.add_argument("--sem-arquivos", action="store_true", help="Não restaura os arquivos do GED.")
        parser.add_argument("--confirmo", default="", help="Digite o NOME DO BANCO de destino para confirmar.")

    def handle(self, *args, arquivo, senha, banco, sem_arquivos, confirmo, **options):
        alvo = banco or settings.DATABASES["default"]["NAME"]
        if confirmo != alvo:
            raise CommandError(f"Isto SUBSTITUI o conteúdo do banco '{alvo}'. Confirme com: --confirmo {alvo}")
        try:
            m = backup.restaurar(Path(arquivo), senha, banco, restaurar_media=not sem_arquivos)
        except backup.BackupErro as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Restaurado o backup de {m['gerado_em']} no banco '{alvo}' ({m['arquivos_restaurados']} arquivo(s) devolvido(s)). "
            "Rode 'python manage.py migrate' se o sistema foi atualizado depois do backup."))
