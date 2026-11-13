"""Atividades do quadro Kanban: individuais, por grupo e acompanhadas pelos gestores."""
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import Colaborador, Departamento, ModeloBase

COLUNAS = [("a_fazer", "A fazer"), ("fazendo", "Em andamento"), ("revisao", "Em revisão"), ("concluida", "Concluída")]


def equipe_de(user):
    """IDs dos usuários geridos por `user`: liderados diretos (Colaborador.gestor) e quem está nos
    departamentos (e subdepartamentos) dos quais ele é responsável."""
    if not user.is_authenticated:
        return set()
    eu = Colaborador.objects.filter(usuario=user).first()
    if eu is None:
        return set()
    deptos = set(Departamento.objects.filter(responsavel=eu).values_list("pk", flat=True))
    novos = set(deptos)
    while novos:  # inclui subdepartamentos em qualquer nível
        novos = set(Departamento.objects.filter(superior__in=novos).values_list("pk", flat=True)) - deptos
        deptos |= novos
    ids = Colaborador.objects.filter(
        Q(gestor=eu) | Q(departamento__in=deptos), usuario__isnull=False, status__in=["ativo", "ferias", "afastado"]
    ).exclude(usuario=user).values_list("usuario_id", flat=True)
    return set(ids)


def e_gestor(user):
    return user.is_superuser or user.has_perm("tarefas.ver_todas_atividade") or bool(equipe_de(user))


class AtividadeQuerySet(models.QuerySet):
    def minhas(self, user):
        return self.filter(Q(responsavel=user) | Q(criado_por=user) |
                           Q(grupo__in=user.groups.all(), responsavel__isnull=True))

    def do_grupo(self, user):
        return self.filter(grupo__in=user.groups.all())

    def da_equipe(self, user):
        return self.filter(responsavel_id__in=equipe_de(user))

    def visiveis_para(self, user):
        if user.is_superuser or user.has_perm("tarefas.ver_todas_atividade"):
            return self.all()
        return self.filter(
            Q(responsavel=user) | Q(criado_por=user) | Q(grupo__in=user.groups.all())
            | Q(responsavel_id__in=equipe_de(user))
        ).distinct()


class Atividade(ModeloBase):
    PRIORIDADES = [("baixa", "Baixa"), ("media", "Média"), ("alta", "Alta"), ("urgente", "Urgente")]
    titulo = models.CharField("título", max_length=200)
    descricao = models.TextField("descrição", blank=True)
    status = models.CharField("coluna", max_length=10, choices=COLUNAS + [("cancelada", "Cancelada")],
                              default="a_fazer", db_index=True)
    prioridade = models.CharField(max_length=8, choices=PRIORIDADES, default="media")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="atividades",
                                    help_text="Vazio = atividade do grupo (qualquer membro pode assumir).")
    grupo = models.ForeignKey(Group, null=True, blank=True, on_delete=models.SET_NULL, related_name="atividades")
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL)
    prazo = models.DateField(null=True, blank=True)
    etiquetas = models.CharField(max_length=150, blank=True, help_text="Separadas por vírgula.")
    ordem = models.PositiveIntegerField(default=0, db_index=True)
    iniciada_em = models.DateTimeField("iniciada em", null=True, blank=True, editable=False)
    concluida_em = models.DateTimeField("concluída em", null=True, blank=True, editable=False)
    # vínculo opcional com outro registro (ex.: contrato a vencer)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, editable=False)
    object_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    vinculo = GenericForeignKey("content_type", "object_id")

    objects = AtividadeQuerySet.as_manager()

    class Meta:
        verbose_name = "atividade"
        verbose_name_plural = "atividades"
        ordering = ["ordem", "prazo", "pk"]
        permissions = [("ver_todas_atividade", "Pode ver e gerir todas as atividades")]

    def __str__(self):
        return self.titulo

    @property
    def atrasada(self):
        return bool(self.prazo and self.status not in ("concluida", "cancelada") and self.prazo < timezone.localdate())

    @property
    def lista_etiquetas(self):
        return [e.strip() for e in self.etiquetas.split(",") if e.strip()]

    def pode_editar(self, user):
        if user.is_superuser or user.has_perm("tarefas.ver_todas_atividade"):
            return True
        if user.pk in (self.responsavel_id, self.criado_por_id):
            return True
        if self.grupo_id and user.groups.filter(pk=self.grupo_id).exists():
            return True
        return self.responsavel_id in equipe_de(user)

    def mudar_status(self, status):
        agora = timezone.now()
        if status == "fazendo" and not self.iniciada_em:
            self.iniciada_em = agora
        self.concluida_em = agora if status == "concluida" else None
        self.status = status


class Comentario(models.Model):
    atividade = models.ForeignKey(Atividade, on_delete=models.CASCADE, related_name="comentarios")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    texto = models.TextField()
    data = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "comentário"
        verbose_name_plural = "comentários"
        ordering = ["data"]

    def __str__(self):
        return f"{self.usuario}: {self.texto[:40]}"
