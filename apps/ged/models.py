"""GED - Gestão Eletrônica de Documentos: pastas com controle de acesso, documentos e versões."""
import hashlib
import os

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import Departamento, ModeloBase


class Pasta(ModeloBase):
    nome = models.CharField(max_length=120)
    superior = models.ForeignKey("self", verbose_name="pasta superior", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="subpastas")
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL)
    grupos_acesso = models.ManyToManyField(Group, verbose_name="grupos com acesso", blank=True,
                                           help_text="Vazio = todos os usuários com acesso ao GED.")

    class Meta:
        verbose_name = "pasta"
        verbose_name_plural = "pastas"
        ordering = ["nome"]
        unique_together = [("superior", "nome")]

    def __str__(self):
        return self.caminho

    @property
    def caminho(self):
        partes, p, vistos = [], self, set()
        while p is not None and p.pk not in vistos:
            vistos.add(p.pk)
            partes.append(p.nome)
            p = p.superior
        return " / ".join(reversed(partes))

    def acessivel_por(self, user):
        if user.is_superuser:
            return True
        p = self
        while p is not None:
            grupos = list(p.grupos_acesso.all())
            if grupos and not user.groups.filter(pk__in=[g.pk for g in grupos]).exists():
                return False
            p = p.superior
        return True

    @classmethod
    def visiveis_para(cls, user):
        if user.is_superuser:
            return cls.objects.all()
        ids = [p.pk for p in cls.objects.prefetch_related("grupos_acesso") if p.acessivel_por(user)]
        return cls.objects.filter(pk__in=ids)


def caminho_arquivo(instance, filename):
    return f"ged/{timezone.now():%Y/%m}/{instance.documento_id}/v{instance.numero}_{filename}"


class Documento(ModeloBase):
    TIPOS = [("contrato", "Contrato"), ("nota", "Nota fiscal / comprovante"), ("rh", "Documento de RH"),
             ("politica", "Política / procedimento"), ("relatorio", "Relatório"), ("juridico", "Jurídico"),
             ("outro", "Outro")]
    pasta = models.ForeignKey(Pasta, on_delete=models.PROTECT, related_name="documentos")
    titulo = models.CharField("título", max_length=200)
    tipo = models.CharField(max_length=20, choices=TIPOS, default="outro")
    descricao = models.TextField("descrição", blank=True)
    tags = models.CharField(max_length=200, blank=True, help_text="Palavras-chave separadas por vírgula.")
    validade = models.DateField(null=True, blank=True, help_text="Para contratos, alvarás, certidões etc.")
    confidencial = models.BooleanField(default=False)

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["titulo"]

    def __str__(self):
        return self.titulo

    @property
    def versao_atual(self):
        return self.versoes.order_by("-numero").first()

    @property
    def vencido(self):
        return bool(self.validade and self.validade < timezone.localdate())

    @property
    def num_versao(self):
        v = self.versao_atual
        return v.numero if v else 0
    num_versao.fget.short_description = "versão"

    def adicionar_versao(self, arquivo, comentario="", usuario=None):
        numero = self.num_versao + 1
        sha = hashlib.sha256()
        for chunk in arquivo.chunks():
            sha.update(chunk)
        arquivo.seek(0)
        return Versao.objects.create(documento=self, numero=numero, arquivo=arquivo, comentario=comentario,
                                     tamanho=arquivo.size, hash_sha256=sha.hexdigest(),
                                     enviado_por=usuario or usuario_atual(), nome_original=arquivo.name[:200])

    @classmethod
    def visiveis_para(cls, user):
        qs = cls.objects.filter(pasta__in=Pasta.visiveis_para(user))
        if not user.is_superuser and not user.has_perm("ged.ver_confidencial"):
            qs = qs.filter(Q(confidencial=False) | Q(criado_por=user))
        return qs


class Versao(models.Model):
    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="versoes")
    numero = models.PositiveIntegerField("número")
    arquivo = models.FileField(upload_to=caminho_arquivo)
    nome_original = models.CharField("nome do arquivo", max_length=200, blank=True)
    tamanho = models.PositiveBigIntegerField(default=0)
    hash_sha256 = models.CharField("SHA-256", max_length=64, blank=True)
    comentario = models.CharField("comentário", max_length=300, blank=True)
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    data = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "versão"
        verbose_name_plural = "versões"
        ordering = ["-numero"]
        unique_together = [("documento", "numero")]
        permissions = [("ver_confidencial", "Pode ver documentos confidenciais de outros usuários")]

    def __str__(self):
        return f"{self.documento} v{self.numero}"

    def get_absolute_url(self):
        return reverse("ged:download", args=[self.pk])

    @property
    def extensao(self):
        return os.path.splitext(self.nome_original or self.arquivo.name)[1].lower()
