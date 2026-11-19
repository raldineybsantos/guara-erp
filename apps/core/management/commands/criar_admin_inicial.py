# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.seguranca.models import TrocaSenhaObrigatoria

USUARIO_PADRAO = "GuaraSuperAdmin"
SENHA_PADRAO = "Admin$Guara"


class Command(BaseCommand):
    help = ("Cria o administrador inicial (GuaraSuperAdmin) com senha provisória, que o sistema obriga a trocar "
            "no primeiro acesso. Só age numa instalação nova: se já existe algum superusuário, não faz nada.")

    def handle(self, *args, **opts):
        User = get_user_model()
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("Já existe um administrador; nada a fazer.")
            return
        usuario, criado = User.objects.get_or_create(username=USUARIO_PADRAO, defaults={
            "is_staff": True, "is_superuser": True, "is_active": True, "first_name": "Administrador"})
        if not criado:
            usuario.is_staff = usuario.is_superuser = usuario.is_active = True
        usuario.set_password(SENHA_PADRAO)
        usuario.save()
        TrocaSenhaObrigatoria.exigir(usuario)
        self.stdout.write(self.style.WARNING(
            f"Administrador inicial criado: usuário {USUARIO_PADRAO} (senha provisória, troca obrigatória no 1º login)."))
