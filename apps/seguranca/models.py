# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.conf import settings
from django.db import models
from django.utils import timezone


class DispositivoMFA(models.Model):
    """Segredo TOTP do usuário, cifrado com a chave do cofre (nunca em texto puro no banco nem no backup)."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa")
    segredo = models.BinaryField(editable=False)
    confirmado = models.BooleanField(default=False)
    ultimo_passo = models.BigIntegerField(default=0, help_text="Último passo de 30 s aceito (impede reutilizar o código).")
    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    confirmado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "dispositivo de dois fatores"
        verbose_name_plural = "dispositivos de dois fatores"

    def __str__(self):
        return f"2FA de {self.usuario}"

    def definir_segredo(self, texto: str):
        from apps.fiscal import cofre

        self.segredo = cofre.cifrar(texto.encode())

    def obter_segredo(self) -> str:
        from apps.fiscal import cofre

        return cofre.decifrar(self.segredo).decode()


class CodigoRecuperacao(models.Model):
    """Código de uso único para quem perdeu o celular. Só a impressão digital (SHA-256) é guardada."""

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="codigos_recuperacao")
    impressao = models.CharField(max_length=64, db_index=True)
    usado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = "código de recuperação"
        verbose_name_plural = "códigos de recuperação"


class RegistroBackup(models.Model):
    """Histórico de cada backup (agendado ou manual): o que foi gerado, onde, se foi verificado e copiado."""

    SITUACOES = [("executando", "Executando"), ("ok", "Concluído"), ("erro", "Falhou")]
    ORIGENS = [("agendado", "Agendado"), ("manual", "Manual")]

    iniciado_em = models.DateTimeField(default=timezone.now, db_index=True)
    concluido_em = models.DateTimeField(null=True, blank=True)
    origem = models.CharField(max_length=10, choices=ORIGENS, default="agendado")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    situacao = models.CharField(max_length=10, choices=SITUACOES, default="executando", db_index=True)
    arquivo = models.CharField(max_length=200, blank=True)
    tamanho = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, help_text="Impressão digital do arquivo cifrado.")
    arquivos_media = models.IntegerField(default=0)
    verificado = models.BooleanField(default=False, help_text="Decifrado, conferido e lido pelo pg_restore --list.")
    copia_externa = models.CharField(max_length=200, blank=True)
    removidos = models.IntegerField(default=0, help_text="Backups antigos removidos pela política de retenção.")
    erro = models.TextField(blank=True)

    class Meta:
        verbose_name = "registro de backup"
        verbose_name_plural = "registros de backup"
        ordering = ["-iniciado_em"]

    def __str__(self):
        return f"Backup {self.iniciado_em:%d/%m/%Y %H:%M} ({self.get_situacao_display()})"
