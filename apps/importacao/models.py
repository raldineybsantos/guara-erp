import os
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import ModeloBase


def _caminho(prefixo):
    def f(instance, nome):
        ext = os.path.splitext(nome)[1].lower()[:8]
        return f"importacoes/{prefixo}/{uuid.uuid4().hex}{ext}"
    return f


def caminho_origem(instance, nome):
    return _caminho("origem")(instance, nome)


def caminho_dados(instance, nome):
    return _caminho("dados")(instance, nome)


class Importacao(ModeloBase):
    STATUS = [("lido", "Dados lidos"), ("mapeado", "Colunas mapeadas"), ("simulado", "Simulada"),
              ("concluida", "Concluída"), ("recusada", "Recusada (com erros)"), ("desfeita", "Desfeita")]
    ORIGENS = [("arquivo", "Arquivo"), ("banco", "Banco de dados")]
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="importacoes")
    origem = models.CharField(max_length=10, choices=ORIGENS, default="arquivo")
    descricao = models.CharField("origem", max_length=200)
    tabela_origem = models.CharField("tabela / planilha", max_length=200, blank=True)
    destino = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="lido")
    colunas = models.JSONField(default=list)
    total_linhas = models.PositiveIntegerField("linhas na origem", default=0)
    lidas = models.PositiveIntegerField("linhas lidas", default=0)
    arquivo_origem = models.FileField(upload_to=caminho_origem, blank=True, null=True, editable=False)
    dados = models.FileField(upload_to=caminho_dados, blank=True, null=True, editable=False)
    mapeamento = models.JSONField(default=dict, blank=True)
    opcoes = models.JSONField(default=dict, blank=True)
    resultado = models.JSONField(default=dict, blank=True)
    criados = models.JSONField(default=list, blank=True)
    usou_ia = models.BooleanField("usou IA", default=False)
    observacao_ia = models.TextField(blank=True)

    class Meta:
        verbose_name = "importação"
        verbose_name_plural = "importações"
        ordering = ["-criado_em"]
        permissions = [("importar_dados", "Importar dados de planilhas e bancos de outros sistemas"),
                       ("conectar_banco", "Conectar a bancos de dados de origem"),
                       ("ver_todas_importacoes", "Ver as importações de todos os usuários")]

    def __str__(self):
        return f"Importação #{self.pk}: {self.descricao}"

    @property
    def truncada(self):
        return self.total_linhas > self.lidas

    def apagar_arquivos(self):
        for campo in ("arquivo_origem", "dados"):
            f = getattr(self, campo)
            if f:
                try:
                    f.delete(save=False)
                except OSError:
                    pass
        self.arquivo_origem = None
        self.dados = None
