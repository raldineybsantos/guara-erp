"""Assistente de IA: interpreta pedidos em linguagem natural ("cadastre o fornecedor X, CNPJ ...") e monta uma
PROPOSTA de cadastro. A IA nunca grava: quem salva é o usuário, pelo formulário normal, com as permissões dele."""

import datetime
import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class ConfiguracaoIA(models.Model):
    PROVEDORES = [("desligado", "Desligado"), ("ollama", "Ollama (local, na rede da empresa)"),
                  ("openai", "API compatível com OpenAI (nuvem ou local)"), ("anthropic", "Anthropic (Claude, nuvem)")]
    provedor = models.CharField(max_length=10, choices=PROVEDORES, default="desligado")
    url_base = models.CharField("endereço do serviço", max_length=200, blank=True,
                                help_text="Ollama: http://servidor:11434 · OpenAI: https://api.openai.com · "
                                          "Anthropic: https://api.anthropic.com (em branco = padrão do provedor)")
    modelo = models.CharField(max_length=80, blank=True, help_text="Nome do modelo no provedor (ex.: llama3.1).")
    chave_api = models.BinaryField(null=True, blank=True, editable=False)
    tempo_limite = models.PositiveSmallIntegerField("tempo limite (s)", default=60)
    servicedesk_triagem = models.BooleanField(
        "Service Desk: classificar e fazer o primeiro atendimento", default=False,
        help_text="A IA escolhe o serviço e a fila do chamado e orienta o usuário com base na biblioteca de "
                  "soluções. Se não resolver, o chamado segue para a equipe.")
    telegram_ativo = models.BooleanField("receber pedidos pelo Telegram", default=False)
    telegram_token = models.BinaryField(null=True, blank=True, editable=False)
    telegram_segredo = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        verbose_name = "configuração da IA"
        verbose_name_plural = "configuração da IA"

    def __str__(self):
        return f"IA: {self.get_provedor_display()}"

    @classmethod
    def atual(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        if not obj.telegram_segredo:
            obj.telegram_segredo = secrets.token_urlsafe(32)
            obj.save(update_fields=["telegram_segredo"])
        return obj

    @property
    def na_nuvem(self):
        local = ("localhost", "127.0.0.1", "10.", "192.168.", "172.")
        if self.provedor == "anthropic":
            return True
        if self.provedor == "openai":
            return not any(x in (self.url_base or "") for x in local)
        return False

    def get_absolute_url(self):
        return reverse("assistente:configuracao")


class SolicitacaoCadastro(models.Model):
    CANAIS = [("erp", "Chat do ERP"), ("telegram", "Telegram")]
    STATUS = [("pendente", "Aguardando conferência"), ("encaminhada", "Aberta no formulário"),
              ("nao_entendida", "Não entendida"), ("descartada", "Descartada")]
    TIPOS = [("cadastro", "Cadastro"), ("consulta", "Consulta")]
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tipo = models.CharField(max_length=8, choices=TIPOS, default="cadastro")
    canal = models.CharField(max_length=10, choices=CANAIS, default="erp")
    texto = models.TextField("pedido")
    alvo = models.CharField("cadastro", max_length=80, blank=True)
    alvo_nome = models.CharField(max_length=120, blank=True)
    dados = models.JSONField(default=dict, blank=True)
    pendencias = models.JSONField(default=list, blank=True)
    resposta = models.TextField(blank=True)
    status = models.CharField(max_length=14, choices=STATUS, default="pendente")
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "pedido ao assistente"
        verbose_name_plural = "pedidos ao assistente"
        ordering = ["-criado_em", "-id"]

    def url_formulario(self):
        from .catalogo import crud_de

        crud = crud_de(self.alvo)
        return f"{reverse(crud.url_name('create'))}?ia={self.pk}" if crud else ""


class VinculoCanal(models.Model):
    """Liga um chat do Telegram a um usuário do ERP. Só chats vinculados são atendidos."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vinculo_ia")
    chat_id = models.CharField(max_length=40, blank=True, db_index=True)
    codigo = models.CharField(max_length=12, blank=True)
    codigo_expira = models.DateTimeField(null=True, blank=True)
    vinculado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "vínculo com o Telegram"
        verbose_name_plural = "vínculos com o Telegram"

    def novo_codigo(self):
        self.codigo = secrets.token_hex(4).upper()
        self.codigo_expira = timezone.now() + datetime.timedelta(minutes=10)
        self.save()
        return self.codigo
