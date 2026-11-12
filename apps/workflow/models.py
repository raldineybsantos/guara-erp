"""Workflow / BPM: processos com etapas sequenciais, aprovações, tarefas e histórico."""
import datetime

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Departamento, ModeloBase


class Processo(ModeloBase):
    nome = models.CharField(max_length=120, unique=True)
    codigo = models.SlugField("código", max_length=50, unique=True,
                              help_text="Identificador usado por integrações, ex.: aprovacao-pagamento")
    descricao = models.TextField("descrição", blank=True)
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "processo (BPM)"
        verbose_name_plural = "processos (BPM)"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def primeira_etapa(self):
        return self.etapas.order_by("ordem").first()

    @transaction.atomic
    def iniciar(self, titulo, solicitante, descricao="", objeto=None):
        etapa = self.primeira_etapa()
        if etapa is None:
            raise ValidationError("O processo não possui etapas.")
        inst = Instancia.objects.create(processo=self, titulo=titulo, descricao=descricao,
                                        solicitante=solicitante, etapa_atual=etapa)
        if objeto is not None:
            inst.content_type = ContentType.objects.get_for_model(objeto)
            inst.object_id = objeto.pk
        inst.prazo = etapa.calcular_prazo()
        inst.save()
        Historico.objects.create(instancia=inst, etapa=etapa, usuario=solicitante, acao="iniciou", comentario=descricao)
        return inst


class Etapa(models.Model):
    TIPOS = [("tarefa", "Tarefa (executar)"), ("aprovacao", "Aprovação (aprovar/rejeitar)")]
    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="etapas")
    ordem = models.PositiveSmallIntegerField(default=1)
    nome = models.CharField(max_length=120)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="tarefa")
    grupo_responsavel = models.ForeignKey(Group, verbose_name="grupo responsável", null=True, blank=True,
                                          on_delete=models.SET_NULL)
    usuario_responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="usuário responsável",
                                            null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    prazo_dias = models.PositiveSmallIntegerField("prazo (dias)", default=2)
    instrucoes = models.TextField("instruções", blank=True)

    class Meta:
        verbose_name = "etapa"
        verbose_name_plural = "etapas"
        ordering = ["processo", "ordem"]

    def __str__(self):
        return f"{self.ordem}. {self.nome}"

    def calcular_prazo(self):
        return timezone.now() + datetime.timedelta(days=self.prazo_dias)

    def proxima(self):
        return self.processo.etapas.filter(ordem__gt=self.ordem).order_by("ordem").first()

    def responsavel_texto(self):
        partes = [str(x) for x in (self.usuario_responsavel, self.grupo_responsavel) if x]
        return " / ".join(partes) or "Qualquer usuário"

    def pode_atuar(self, user):
        if user.is_superuser:
            return True
        if not self.usuario_responsavel_id and not self.grupo_responsavel_id:
            return True
        if self.usuario_responsavel_id == user.pk:
            return True
        return bool(self.grupo_responsavel_id and user.groups.filter(pk=self.grupo_responsavel_id).exists())


class InstanciaQuerySet(models.QuerySet):
    def visiveis_para(self, user):
        """Cada usuário vê o que abriu, o que está com ele e o que já tratou; gestores veem tudo."""
        if user.is_superuser or user.has_perm("workflow.ver_todas_instancia"):
            return self.all()
        return self.filter(
            Q(solicitante=user) | Q(historico__usuario=user) | Q(pk__in=self.pendentes_para(user).values("pk"))
        ).distinct()

    def pendentes_para(self, user):
        qs = self.filter(status="andamento")
        if user.is_superuser:
            return qs
        return qs.filter(
            Q(etapa_atual__usuario_responsavel=user)
            | Q(etapa_atual__grupo_responsavel__in=user.groups.all())
            | Q(etapa_atual__usuario_responsavel__isnull=True, etapa_atual__grupo_responsavel__isnull=True)
        ).distinct()


class Instancia(ModeloBase):
    STATUS = [("andamento", "Em andamento"), ("concluido", "Concluído"), ("rejeitado", "Rejeitado"),
              ("cancelado", "Cancelado")]
    processo = models.ForeignKey(Processo, on_delete=models.PROTECT, related_name="instancias")
    titulo = models.CharField("título", max_length=200)
    descricao = models.TextField("descrição", blank=True)
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="solicitacoes")
    etapa_atual = models.ForeignKey(Etapa, verbose_name="etapa atual", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS, default="andamento", db_index=True)
    prazo = models.DateTimeField("prazo da etapa", null=True, blank=True)
    concluido_em = models.DateTimeField("concluído em", null=True, blank=True)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, editable=False)
    object_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    objeto = GenericForeignKey("content_type", "object_id")

    objects = InstanciaQuerySet.as_manager()

    class Meta:
        verbose_name = "solicitação (instância)"
        verbose_name_plural = "solicitações"
        ordering = ["-criado_em"]
        permissions = [("ver_todas_instancia", "Pode ver todas as solicitações de workflow")]

    def __str__(self):
        return f"#{self.pk} {self.titulo}"

    @property
    def atrasada(self):
        return self.status == "andamento" and self.prazo and self.prazo < timezone.now()

    @transaction.atomic
    def avancar(self, usuario, acao, comentario=""):
        """acao: 'concluir' (tarefa), 'aprovar', 'rejeitar' ou 'cancelar'."""
        if self.status != "andamento":
            raise ValidationError("Esta solicitação já foi finalizada.")
        etapa = self.etapa_atual
        if acao == "cancelar":
            if usuario != self.solicitante and not usuario.is_superuser:
                raise ValidationError("Somente o solicitante pode cancelar.")
            self.status = "cancelado"
            self.concluido_em = timezone.now()
            self.save()
            Historico.objects.create(instancia=self, etapa=etapa, usuario=usuario, acao="cancelou", comentario=comentario)
            return
        if not etapa.pode_atuar(usuario):
            raise ValidationError("Você não é responsável por esta etapa.")
        if acao == "rejeitar":
            if etapa.tipo != "aprovacao":
                raise ValidationError("Etapa de tarefa não pode ser rejeitada.")
            if not comentario:
                raise ValidationError("Informe o motivo da rejeição.")
            self.status = "rejeitado"
            self.concluido_em = timezone.now()
            self.save()
            Historico.objects.create(instancia=self, etapa=etapa, usuario=usuario, acao="rejeitou", comentario=comentario)
            return
        Historico.objects.create(instancia=self, etapa=etapa, usuario=usuario,
                                 acao="aprovou" if acao == "aprovar" else "concluiu", comentario=comentario)
        proxima = etapa.proxima()
        if proxima:
            self.etapa_atual = proxima
            self.prazo = proxima.calcular_prazo()
        else:
            self.status = "concluido"
            self.concluido_em = timezone.now()
            self._ao_concluir()
        self.save()

    def _ao_concluir(self):
        """Integração: ao concluir um fluxo de aprovação ligado a um registro com campo `aprovado`."""
        obj = self.objeto
        if obj is not None and hasattr(obj, "aprovado"):
            obj.aprovado = True
            obj.save()


class Historico(models.Model):
    ACOES = [("iniciou", "Iniciou"), ("concluiu", "Concluiu"), ("aprovou", "Aprovou"), ("rejeitou", "Rejeitou"),
             ("cancelou", "Cancelou"), ("comentou", "Comentou")]
    instancia = models.ForeignKey(Instancia, on_delete=models.CASCADE, related_name="historico")
    etapa = models.ForeignKey(Etapa, null=True, blank=True, on_delete=models.SET_NULL)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    acao = models.CharField("ação", max_length=10, choices=ACOES)
    comentario = models.TextField("comentário", blank=True)
    data = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "histórico"
        verbose_name_plural = "históricos"
        ordering = ["data"]

    def __str__(self):
        return f"{timezone.localtime(self.data):%d/%m/%Y %H:%M} {self.usuario} {self.get_acao_display()}"
