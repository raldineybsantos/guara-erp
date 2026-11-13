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
        inst = Instancia(processo=self, titulo=titulo, descricao=descricao, solicitante=solicitante)
        if objeto is not None:
            inst.content_type = ContentType.objects.get_for_model(objeto)
            inst.object_id = objeto.pk
        etapa = inst._etapa_valida(etapa, objeto)
        if etapa is None:
            raise ValidationError("Nenhuma etapa do processo se aplica a este registro.")
        inst.etapa_atual = etapa
        inst.responsavel_atual = inst._responsavel_dinamico(etapa, objeto)
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
            Q(responsavel_atual=user)
            | Q(responsavel_atual__isnull=True) & (
                Q(etapa_atual__usuario_responsavel=user)
                | Q(etapa_atual__grupo_responsavel__in=user.groups.all())
                | Q(etapa_atual__usuario_responsavel__isnull=True, etapa_atual__grupo_responsavel__isnull=True))
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
    # Aprovador designado pelo registro vinculado (ex.: coordenador do departamento do pedido de compra).
    # Quando preenchido, só ele (ou um superusuário) atua na etapa atual.
    responsavel_atual = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável designado",
                                          null=True, blank=True, on_delete=models.PROTECT, related_name="+")
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
    def responsavel_texto(self):
        if self.responsavel_atual_id:
            u = self.responsavel_atual
            return u.get_full_name() or u.username
        return self.etapa_atual.responsavel_texto() if self.etapa_atual_id else ""

    def pode_atuar(self, user):
        if self.responsavel_atual_id:
            return user.is_superuser or user.pk == self.responsavel_atual_id
        return bool(self.etapa_atual_id) and self.etapa_atual.pode_atuar(user)

    # Ganchos do registro vinculado:
    #   workflow_pular(etapa) -> True para saltar a etapa (ex.: nível acima da alçada necessária)
    #   workflow_responsavel(etapa) -> usuário que deve atuar na etapa
    def _etapa_valida(self, etapa, objeto=None):
        objeto = objeto if objeto is not None else self.objeto
        while etapa is not None and objeto is not None and hasattr(objeto, "workflow_pular") \
                and objeto.workflow_pular(etapa):
            etapa = etapa.proxima()
        return etapa

    def _responsavel_dinamico(self, etapa, objeto=None):
        objeto = objeto if objeto is not None else self.objeto
        if objeto is not None and hasattr(objeto, "workflow_responsavel"):
            return objeto.workflow_responsavel(etapa)
        return None

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
            self._avisar_objeto("workflow_cancelado", comentario)
            return
        if not self.pode_atuar(usuario):
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
            self._avisar_objeto("workflow_recusado", comentario)
            return
        Historico.objects.create(instancia=self, etapa=etapa, usuario=usuario,
                                 acao="aprovou" if acao == "aprovar" else "concluiu", comentario=comentario)
        proxima = self._etapa_valida(etapa.proxima())
        if proxima:
            self.etapa_atual = proxima
            self.responsavel_atual = self._responsavel_dinamico(proxima)
            self.prazo = proxima.calcular_prazo()
        else:
            self.status = "concluido"
            self.concluido_em = timezone.now()
            self._ao_concluir()
        self.save()

    def _ao_concluir(self):
        """Integração: o registro vinculado pode reagir com `workflow_aprovado(instancia)`;
        senão, se tiver o campo `aprovado` (ex.: conta a pagar), ele é marcado."""
        obj = self.objeto
        if obj is None:
            return
        if hasattr(obj, "workflow_aprovado"):
            obj.workflow_aprovado(self)
        elif hasattr(obj, "aprovado"):
            obj.aprovado = True
            obj.save()

    def _avisar_objeto(self, metodo, comentario=""):
        obj = self.objeto
        if obj is not None and hasattr(obj, metodo):
            getattr(obj, metodo)(self, comentario)


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
