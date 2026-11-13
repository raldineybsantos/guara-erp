"""Service Desk: áreas de atendimento, catálogo de serviços com SLA, chamados com aprovação, chat e
biblioteca de soluções, e gestão de mudanças (RDM) com aprovação do gestor e do CAB."""

import datetime
import re
import unicodedata

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase, notificar
from apps.core.numeracao import NumeradoMixin

from . import sla

PRIORIDADES = [("baixa", "Baixa"), ("media", "Média"), ("alta", "Alta"), ("critica", "Crítica")]
FATOR_PRIORIDADE = {"baixa": 1.5, "media": 1.0, "alta": 0.5, "critica": 0.25}


# --------------------------------------------------------------------------- áreas e catálogo
class Area(ModeloBase):
    nome = models.CharField(max_length=80, unique=True, help_text="Ex.: TI, Facilities, RH, Financeiro")
    grupo = models.ForeignKey(Group, verbose_name="equipe de atendimento", on_delete=models.PROTECT,
                              related_name="areas_servicedesk",
                              help_text="Membros deste grupo atendem os chamados da área.")
    gestor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="gestor da área", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+",
                               help_text="Recebe os avisos de SLA vencido e aprova mudanças da área.")
    icone = models.CharField("ícone", max_length=40, default="headset",
                             help_text="Nome de ícone Bootstrap Icons (ex.: laptop, building, people).")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "área de atendimento"
        verbose_name_plural = "áreas de atendimento"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def equipe(self):
        return list(self.grupo.user_set.filter(is_active=True))

    def atende(self, user):
        return user.is_superuser or self.grupo.user_set.filter(pk=user.pk).exists()


class Servico(ModeloBase):
    APROVADORES = [("nenhum", "Não precisa de aprovação"), ("gestor_solicitante", "Gestor do solicitante"),
                   ("gestor_area", "Gestor da área"), ("usuario", "Usuário específico")]
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="servicos")
    nome = models.CharField(max_length=120)
    descricao = models.TextField("descrição", blank=True)
    palavras_chave = models.CharField("palavras-chave", max_length=250, blank=True,
                                      help_text="Ajudam o assistente a sugerir este serviço.")
    instrucoes = models.TextField("o que informar ao abrir", blank=True,
                                  help_text="Aparece no formulário do chamado. Ex.: patrimônio do equipamento.")
    prioridade_padrao = models.CharField("prioridade padrão", max_length=8, choices=PRIORIDADES, default="media")
    sla_resposta_horas = models.DecimalField("SLA de 1ª resposta (h)", max_digits=6, decimal_places=2, default=4)
    sla_solucao_horas = models.DecimalField("SLA de solução (h)", max_digits=6, decimal_places=2, default=16,
                                            help_text="Para prioridade média. Alta = metade; crítica = 1/4; "
                                                      "baixa = 1,5×. Conta em horário comercial.")
    aprovacao = models.CharField("aprovação", max_length=20, choices=APROVADORES, default="nenhum")
    aprovador = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="aprovador (usuário específico)", null=True,
                                  blank=True, on_delete=models.SET_NULL, related_name="+")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "serviço do catálogo"
        verbose_name_plural = "catálogo de serviços"
        ordering = ["area__nome", "nome"]

    def __str__(self):
        return f"{self.area} › {self.nome}"


# --------------------------------------------------------------------------- chamado
class ChamadoQuerySet(models.QuerySet):
    def visiveis_para(self, user):
        if user.is_superuser or user.has_perm("servicedesk.ver_todos_chamado"):
            return self.all()
        return self.filter(Q(solicitante=user) | Q(area__grupo__in=user.groups.all()) | Q(area__gestor=user)
                           | Q(aprovador=user)).distinct()

    def em_andamento(self):
        return self.filter(status__in=("aberto", "atendimento", "aguardando_usuario"))


class Chamado(NumeradoMixin, ModeloBase):
    PREFIXO = "SD"
    STATUS = [("aguardando_aprovacao", "Aguardando aprovação"), ("aberto", "Aberto"),
              ("atendimento", "Em atendimento"), ("aguardando_usuario", "Aguardando usuário"),
              ("resolvido", "Resolvido"), ("fechado", "Fechado"), ("recusado", "Recusado"),
              ("cancelado", "Cancelado")]
    ATIVOS = ("aberto", "atendimento", "aguardando_usuario")
    servico = models.ForeignKey(Servico, verbose_name="serviço", on_delete=models.PROTECT, related_name="chamados",
                                limit_choices_to={"ativo": True, "area__ativo": True})
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="chamados", editable=False)
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chamados",
                                    editable=False)
    titulo = models.CharField("resumo", max_length=150)
    descricao = models.TextField("descrição")
    prioridade = models.CharField(max_length=8, choices=PRIORIDADES, blank=True,
                                  help_text="Em branco = prioridade padrão do serviço.")
    status = models.CharField(max_length=20, choices=STATUS, default="aberto", editable=False, db_index=True)
    analista = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="chamados_atendidos", editable=False)
    aprovador = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+", editable=False)
    sla_iniciado_em = models.DateTimeField("SLA iniciado em", null=True, blank=True, editable=False)
    prazo_resposta = models.DateTimeField("prazo da 1ª resposta", null=True, blank=True, editable=False)
    prazo_solucao = models.DateTimeField("prazo de solução", null=True, blank=True, editable=False, db_index=True)
    primeira_resposta_em = models.DateTimeField("1ª resposta em", null=True, blank=True, editable=False)
    pausado_em = models.DateTimeField(null=True, blank=True, editable=False)
    restante_seg = models.PositiveIntegerField(default=0, editable=False)
    resolvido_em = models.DateTimeField("resolvido em", null=True, blank=True, editable=False)
    fechado_em = models.DateTimeField("fechado em", null=True, blank=True, editable=False)
    solucao = models.TextField("solução aplicada", blank=True, editable=False)
    artigo = models.ForeignKey("ArtigoConhecimento", verbose_name="artigo usado", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="chamados", editable=False)
    avaliacao = models.PositiveSmallIntegerField("avaliação (1 a 5)", null=True, blank=True, editable=False)
    comentario_avaliacao = models.TextField("comentário da avaliação", blank=True, editable=False)
    alerta_risco = models.BooleanField(default=False, editable=False)
    alerta_vencido = models.BooleanField(default=False, editable=False)
    alerta_resposta = models.BooleanField(default=False, editable=False)

    objects = ChamadoQuerySet.as_manager()

    class Meta:
        verbose_name = "chamado"
        verbose_name_plural = "chamados"
        ordering = ["-numero"]
        permissions = [("ver_todos_chamado", "Pode ver todos os chamados")]

    def __str__(self):
        return f"{self.numero} {self.titulo}"

    def url(self):
        return reverse("servicedesk:chamado_detail", args=[self.pk])

    # ------------------------------------------------------------- abertura e aprovação
    def save(self, *args, **kwargs):
        novo = self.pk is None
        if novo:
            self.area_id = self.servico.area_id
            self.solicitante = self.solicitante if self.solicitante_id else usuario_atual()
            self.prioridade = self.prioridade or self.servico.prioridade_padrao
        super().save(*args, **kwargs)
        if novo:
            self._iniciar()

    def _definir_aprovador(self):
        s = self.servico
        if s.aprovacao == "gestor_area":
            return s.area.gestor
        if s.aprovacao == "usuario":
            return s.aprovador
        if s.aprovacao == "gestor_solicitante":
            colab = getattr(self.solicitante, "colaborador", None)
            gestor = colab.gestor if colab and colab.gestor_id else None
            if gestor is None and colab and colab.departamento_id and colab.departamento.responsavel_id:
                gestor = colab.departamento.responsavel
            return getattr(gestor, "usuario", None)
        return None

    def _iniciar(self):
        from apps.workflow.models import Processo

        aprovador = self._definir_aprovador() if self.servico.aprovacao != "nenhum" else None
        if self.servico.aprovacao != "nenhum" and aprovador and aprovador != self.solicitante:
            processo = Processo.objects.filter(codigo="aprovacao-servico", ativo=True).first()
            if processo is None:
                raise ValidationError("Processo 'aprovacao-servico' não encontrado. Rode `python manage.py setup_erp`.")
            self.aprovador, self.status = aprovador, "aguardando_aprovacao"
            self.save(update_fields=["aprovador", "status"])
            processo.iniciar(f"Aprovar {self.numero}: {self.titulo}"[:200], self.solicitante,
                             f"Serviço: {self.servico}\nSolicitante: {self._nome(self.solicitante)}\n\n{self.descricao}",
                             objeto=self)
            notificar([aprovador], f"{self.numero} aguarda sua aprovação: {self.titulo}", "/workflow/tarefas/",
                      email=True)
        else:
            self._abrir_sla()

    def _abrir_sla(self):
        agora = timezone.now()
        fator = FATOR_PRIORIDADE[self.prioridade]
        self.status, self.sla_iniciado_em = "aberto", agora
        self.prazo_resposta = sla.somar(agora, int(float(self.servico.sla_resposta_horas) * fator * 3600))
        self.prazo_solucao = sla.somar(agora, int(float(self.servico.sla_solucao_horas) * fator * 3600))
        self.save(update_fields=["status", "sla_iniciado_em", "prazo_resposta", "prazo_solucao"])
        notificar(self.area.equipe(), f"Novo chamado {self.numero} ({self.get_prioridade_display()}): {self.titulo}",
                  self.url(), email=self.prioridade == "critica")

    def workflow_responsavel(self, etapa):
        return self.aprovador

    def workflow_aprovado(self, instancia):
        self._abrir_sla()
        notificar([self.solicitante], f"{self.numero} foi aprovado e está na fila de {self.area}.", self.url())

    def workflow_recusado(self, instancia, comentario):
        self.status = "recusado"
        self.save(update_fields=["status"])
        self.mensagens.create(autor=self.aprovador, texto=f"Recusado pelo aprovador: {comentario}")
        notificar([self.solicitante], f"{self.numero} foi recusado: {comentario}", self.url(), email=True)

    def workflow_cancelado(self, instancia, comentario):
        self.status = "cancelado"
        self.save(update_fields=["status"])

    # ------------------------------------------------------------- SLA
    @property
    def prazo_vigente(self):
        if self.status == "aguardando_usuario":
            return None
        return self.prazo_solucao

    def sla_situacao(self):
        if not self.prazo_solucao:
            return "—"
        if self.status in ("resolvido", "fechado"):
            return "Cumprido" if self.resolvido_em and self.resolvido_em <= self.prazo_solucao else "Violado"
        if self.status == "aguardando_usuario":
            return "Pausado"
        if self.status not in self.ATIVOS:
            return "—"
        agora = timezone.now()
        if agora > self.prazo_solucao:
            return "Vencido"
        total = sla.entre(self.sla_iniciado_em, self.prazo_solucao) or 1
        return "Em risco" if sla.entre(self.sla_iniciado_em, agora) / total >= 0.8 else "No prazo"
    sla_situacao.short_description = "SLA"

    @property
    def status_sla(self):  # etiqueta colorida
        s = self.sla_situacao()
        return {"Vencido": "Vencido", "Violado": "Vencido (violado)", "Em risco": "Atenção: em risco",
                "Pausado": "Aguardando usuário", "Cumprido": "Concluído no prazo"}.get(s, s)

    # ------------------------------------------------------------- atendimento
    def _exigir_equipe(self, user):
        if not self.area.atende(user):
            raise ValidationError(f"Só a equipe de {self.area} atende este chamado.")

    def _nome(self, u):
        return (u.get_full_name() or u.username) if u else "—"

    def assumir(self, analista):
        self._exigir_equipe(analista)
        if self.status not in ("aberto", "atendimento"):
            raise ValidationError("Só chamados abertos ou em atendimento podem ser assumidos.")
        anterior = self.analista
        self.analista, self.status = analista, "atendimento"
        self.primeira_resposta_em = self.primeira_resposta_em or timezone.now()
        self.save(update_fields=["analista", "status", "primeira_resposta_em"])
        self.mensagens.create(autor=analista, texto=f"{self._nome(analista)} assumiu o chamado.", sistema=True)
        if anterior and anterior != analista:
            notificar([anterior], f"{self.numero} foi assumido por {self._nome(analista)}.", self.url())
        notificar([self.solicitante], f"{self._nome(analista)} está atendendo o {self.numero}.", self.url())

    def enviar_mensagem(self, autor, texto, interna=False):
        """Chat do chamado. Mensagem interna só a equipe vê. Resposta do usuário retoma o SLA."""
        texto = (texto or "").strip()
        if not texto:
            raise ValidationError("Mensagem vazia.")
        da_equipe = self.area.atende(autor)
        if autor != self.solicitante and not da_equipe:
            raise ValidationError("Você não participa deste chamado.")
        if self.status in ("fechado", "cancelado", "recusado"):
            raise ValidationError("Chamado encerrado: abra um novo se precisar.")
        msg = self.mensagens.create(autor=autor, texto=texto, interna=interna and da_equipe)
        if da_equipe and autor != self.solicitante:
            if not self.primeira_resposta_em and not msg.interna:
                self.primeira_resposta_em = timezone.now()
                self.save(update_fields=["primeira_resposta_em"])
            if not msg.interna:
                notificar([self.solicitante], f"{self._nome(autor)} respondeu no {self.numero}: {texto[:80]}",
                          self.url())
        else:
            if self.status == "aguardando_usuario":
                self.retomar()
            notificar([self.analista] if self.analista else self.area.equipe(),
                      f"{self._nome(autor)} escreveu no {self.numero}: {texto[:80]}", self.url())
        return msg

    def chamar_usuario(self, analista, texto):
        """O analista pede algo ao usuário: pausa o SLA e avisa por tela e e-mail."""
        self._exigir_equipe(analista)
        if self.status not in ("aberto", "atendimento"):
            raise ValidationError("O chamado precisa estar aberto ou em atendimento.")
        self.analista = self.analista or analista
        self.primeira_resposta_em = self.primeira_resposta_em or timezone.now()
        agora = timezone.now()
        self.restante_seg = sla.entre(agora, self.prazo_solucao) if self.prazo_solucao else 0
        self.status, self.pausado_em = "aguardando_usuario", agora
        self.save(update_fields=["analista", "primeira_resposta_em", "restante_seg", "status", "pausado_em"])
        self.mensagens.create(autor=analista, texto=texto)
        notificar([self.solicitante], f"{self._nome(analista)} precisa de você no {self.numero}: {texto[:90]}",
                  self.url(), email=True)

    def retomar(self):
        if self.status != "aguardando_usuario":
            return
        self.prazo_solucao = sla.somar(timezone.now(), self.restante_seg)
        self.status, self.pausado_em = "atendimento" if self.analista_id else "aberto", None
        self.alerta_risco = self.alerta_vencido = False
        self.save(update_fields=["prazo_solucao", "status", "pausado_em", "alerta_risco", "alerta_vencido"])

    def resolver(self, analista, solucao, artigo=None):
        self._exigir_equipe(analista)
        if self.status not in self.ATIVOS:
            raise ValidationError("Só chamados em andamento podem ser resolvidos.")
        if not solucao.strip():
            raise ValidationError("Descreva a solução aplicada.")
        if self.status == "aguardando_usuario":
            self.retomar()
        agora = timezone.now()
        self.status, self.solucao, self.artigo, self.resolvido_em = "resolvido", solucao, artigo, agora
        self.analista = self.analista or analista
        self.primeira_resposta_em = self.primeira_resposta_em or agora
        self.save()
        self.mensagens.create(autor=analista, texto=f"Solução: {solucao}")
        notificar([self.solicitante], f"{self.numero} foi resolvido. Confirme e avalie, ou reabra se não resolveu.",
                  self.url(), email=True)

    def fechar(self, usuario, avaliacao=None, comentario=""):
        if self.status != "resolvido":
            raise ValidationError("Só chamados resolvidos podem ser fechados.")
        if usuario != self.solicitante and not usuario.is_superuser:
            raise ValidationError("Quem confirma a solução é o solicitante.")
        self.status, self.fechado_em = "fechado", timezone.now()
        self.avaliacao = avaliacao
        self.comentario_avaliacao = comentario
        self.save()

    def reabrir(self, usuario, motivo):
        if self.status != "resolvido":
            raise ValidationError("Só chamados resolvidos podem ser reabertos.")
        if usuario != self.solicitante and not self.area.atende(usuario):
            raise ValidationError("Você não participa deste chamado.")
        self.status, self.resolvido_em = "atendimento" if self.analista_id else "aberto", None
        self.save(update_fields=["status", "resolvido_em"])
        self.mensagens.create(autor=usuario, texto=f"Reaberto: {motivo}")
        notificar([self.analista] if self.analista else self.area.equipe(), f"{self.numero} foi reaberto: {motivo}",
                  self.url(), email=True)

    def cancelar(self, usuario, motivo):
        if self.status in ("resolvido", "fechado", "cancelado", "recusado"):
            raise ValidationError("Chamado já encerrado.")
        if usuario != self.solicitante and not self.area.atende(usuario):
            raise ValidationError("Você não participa deste chamado.")
        self.status = "cancelado"
        self.save(update_fields=["status"])
        self.mensagens.create(autor=usuario, texto=f"Cancelado: {motivo}")

    def transferir(self, analista, nova_area, motivo):
        """Manda para outra área (ex.: o problema era de Facilities, não de TI); o SLA continua."""
        self._exigir_equipe(analista)
        if nova_area == self.area:
            raise ValidationError("O chamado já é desta área.")
        self.area, self.analista = nova_area, None
        if self.status == "atendimento":
            self.status = "aberto"
        self.save(update_fields=["area", "analista", "status"])
        self.mensagens.create(autor=analista, texto=f"Transferido para {nova_area}: {motivo}", sistema=True)
        notificar(nova_area.equipe(), f"{self.numero} transferido para {nova_area}: {self.titulo}", self.url())


class Mensagem(models.Model):
    chamado = models.ForeignKey(Chamado, on_delete=models.CASCADE, related_name="mensagens")
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    texto = models.TextField()
    interna = models.BooleanField("nota interna", default=False)
    sistema = models.BooleanField(default=False)
    data = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["data", "id"]


def verificar_slas(agora=None):
    """Rotina frequente (a cada 15 min no agendador): avisa SLA em risco, 1ª resposta e solução vencidas,
    e fecha os chamados resolvidos que o usuário não confirmou no prazo."""
    agora = agora or timezone.now()
    avisos = 0
    for c in Chamado.objects.em_andamento().exclude(status="aguardando_usuario").select_related("area", "analista"):
        destino = [c.analista] if c.analista_id else c.area.equipe()
        if not c.primeira_resposta_em and c.prazo_resposta and agora > c.prazo_resposta and not c.alerta_resposta:
            notificar(destino, f"1ª resposta vencida no {c.numero}: {c.titulo}", c.url(), email=True)
            c.alerta_resposta = True
            avisos += 1
        if c.prazo_solucao and agora > c.prazo_solucao and not c.alerta_vencido:
            notificar(destino + [c.area.gestor], f"SLA vencido no {c.numero} ({c.area}): {c.titulo}", c.url(),
                      email=True)
            c.alerta_vencido = c.alerta_risco = True
            avisos += 1
        elif c.prazo_solucao and not c.alerta_risco and c.sla_iniciado_em:
            total = sla.entre(c.sla_iniciado_em, c.prazo_solucao) or 1
            if sla.entre(c.sla_iniciado_em, agora) / total >= 0.8:
                notificar(destino, f"SLA em risco no {c.numero}: vence {timezone.localtime(c.prazo_solucao):%d/%m %H:%M}",
                          c.url())
                c.alerta_risco = True
                avisos += 1
        c.save(update_fields=["alerta_resposta", "alerta_vencido", "alerta_risco"])
    limite = agora - datetime.timedelta(days=settings.SERVICEDESK_DIAS_FECHAMENTO)
    fechados = Chamado.objects.filter(status="resolvido", resolvido_em__lt=limite).update(status="fechado",
                                                                                            fechado_em=agora)
    return avisos, fechados


# --------------------------------------------------------------------------- biblioteca de soluções
PALAVRAS_VAZIAS = set("""a o e é de do da dos das em no na nos nas um uma uns umas para por com sem que se
meu minha não nao mais muito como ao aos à às ou eu me está esta estou foi ser ter tem tá ta já ja""".split())


def termos(texto):
    texto = unicodedata.normalize("NFKD", (texto or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return [t for t in re.findall(r"[a-z0-9]+", texto) if len(t) >= 3 and t not in PALAVRAS_VAZIAS]


def _pontuar(campos_pesos, palavras):
    alvo = {peso: set(termos(texto)) for texto, peso in campos_pesos}
    pontos = 0
    for p in palavras:
        for peso, conj in alvo.items():
            if p in conj or any(t.startswith(p) or p.startswith(t) for t in conj if len(t) >= 5 and len(p) >= 5):
                pontos += peso
    return pontos


class ArtigoConhecimento(ModeloBase):
    STATUS = [("rascunho", "Rascunho"), ("publicado", "Publicado"), ("arquivado", "Arquivado")]
    titulo = models.CharField("título", max_length=150)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.SET_NULL, related_name="artigos")
    servico = models.ForeignKey(Servico, verbose_name="serviço", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="artigos")
    sintoma = models.TextField("problema / sintomas", help_text="Como o usuário descreve o problema.")
    solucao = models.TextField("solução passo a passo")
    palavras_chave = models.CharField("palavras-chave", max_length=250, blank=True)
    publico = models.BooleanField("visível para os usuários", default=True,
                                  help_text="Desmarcado: procedimento interno, só as equipes de atendimento veem.")
    status = models.CharField(max_length=10, choices=STATUS, default="publicado")
    visualizacoes = models.PositiveIntegerField(default=0, editable=False)
    util_sim = models.PositiveIntegerField("resolveu", default=0, editable=False)
    util_nao = models.PositiveIntegerField("não resolveu", default=0, editable=False)
    chamado_origem = models.ForeignKey(Chamado, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                       editable=False)

    class Meta:
        verbose_name = "artigo"
        verbose_name_plural = "biblioteca de soluções"
        ordering = ["titulo"]

    def __str__(self):
        return self.titulo

    @classmethod
    def visiveis_para(cls, user):
        qs = cls.objects.filter(status="publicado")
        if user.is_superuser or Area.objects.filter(grupo__in=user.groups.all()).exists():
            return qs
        return qs.filter(publico=True)

    @classmethod
    def buscar(cls, texto, user, limite=3):
        palavras = termos(texto)
        if not palavras:
            return []
        ranking = []
        for a in cls.visiveis_para(user).select_related("area"):
            pts = _pontuar([(a.titulo, 3), (a.palavras_chave, 3), (a.sintoma, 1)], palavras)
            if pts:
                ranking.append((pts + a.util_sim * 0.1 - a.util_nao * 0.1, a))
        ranking.sort(key=lambda x: -x[0])
        return [a for _, a in ranking[:limite]]


def sugerir_servicos(texto, limite=3):
    palavras = termos(texto)
    ranking = []
    for s in Servico.objects.filter(ativo=True, area__ativo=True).select_related("area"):
        pts = _pontuar([(s.nome, 3), (s.palavras_chave, 3), (s.descricao, 1), (s.area.nome, 1)], palavras)
        if pts:
            ranking.append((pts, s))
    ranking.sort(key=lambda x: -x[0])
    return [s for _, s in ranking[:limite]]


# --------------------------------------------------------------------------- gestão de mudanças
class Mudanca(NumeradoMixin, ModeloBase):
    PREFIXO = "RDM"
    TIPOS = [("padrao", "Padrão (pré-aprovada)"), ("normal", "Normal"), ("emergencial", "Emergencial")]
    RISCOS = [("baixo", "Baixo"), ("medio", "Médio"), ("alto", "Alto")]
    STATUS = [("rascunho", "Rascunho"), ("em_aprovacao", "Em aprovação"), ("aprovada", "Aprovada"),
              ("recusada", "Recusada"), ("execucao", "Em execução"), ("concluida", "Concluída"),
              ("falhou", "Falhou (revertida)"), ("cancelada", "Cancelada")]
    titulo = models.CharField("título", max_length=150)
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="mudancas")
    tipo = models.CharField(max_length=12, choices=TIPOS, default="normal")
    risco = models.CharField(max_length=6, choices=RISCOS, default="medio")
    descricao = models.TextField("o que muda")
    justificativa = models.TextField()
    impacto = models.TextField("impacto (o que fica indisponível, quem é afetado)")
    plano_implantacao = models.TextField("plano de implantação")
    plano_testes = models.TextField("plano de testes", blank=True)
    plano_retorno = models.TextField("plano de retorno (rollback)", blank=True)
    janela_inicio = models.DateTimeField("início da janela")
    janela_fim = models.DateTimeField("fim da janela")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável pela execução",
                                    on_delete=models.PROTECT, related_name="+")
    chamados = models.ManyToManyField(Chamado, blank=True, related_name="mudancas",
                                      help_text="Chamados que motivaram a mudança ou serão avisados do resultado.")
    status = models.CharField(max_length=12, choices=STATUS, default="rascunho", editable=False, db_index=True)
    resultado = models.TextField(blank=True, editable=False)
    executada_em = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "mudança (RDM)"
        verbose_name_plural = "mudanças (RDM)"
        ordering = ["-janela_inicio"]

    def clean(self):
        erros = {}
        if self.janela_inicio and self.janela_fim and self.janela_fim <= self.janela_inicio:
            erros["janela_fim"] = "O fim da janela precisa ser depois do início."
        if self.tipo != "padrao" and not (self.plano_retorno or "").strip():
            erros["plano_retorno"] = "Mudança normal ou emergencial exige plano de retorno."
        if self.risco == "alto" and not (self.plano_testes or "").strip():
            erros["plano_testes"] = "Risco alto exige plano de testes."
        if erros:
            raise ValidationError(erros)

    def conflitos(self):
        """Outras mudanças da mesma área com janela sobreposta."""
        return (Mudanca.objects.filter(area=self.area, janela_inicio__lt=self.janela_fim,
                                       janela_fim__gt=self.janela_inicio,
                                       status__in=("em_aprovacao", "aprovada", "execucao"))
                .exclude(pk=self.pk))

    def _mudar(self, status, texto=""):
        self.status = status
        if texto:
            self.resultado = texto
        self.save(update_fields=["status", "resultado"])

    def enviar_para_aprovacao(self, usuario):
        from apps.workflow.models import Processo

        if self.status not in ("rascunho", "recusada"):
            raise ValidationError("Só mudanças em rascunho ou recusadas podem ir para aprovação.")
        self.full_clean()
        if self.tipo == "padrao":
            self._mudar("aprovada", "Mudança padrão: pré-aprovada.")
            return None
        processo = Processo.objects.filter(codigo="aprovacao-mudanca", ativo=True).first()
        if processo is None:
            raise ValidationError("Processo 'aprovacao-mudanca' não encontrado. Rode `python manage.py setup_erp`.")
        aviso = ""
        if self.conflitos().exists():
            aviso = "\n\nATENÇÃO: janela sobreposta com " + ", ".join(str(m.numero) for m in self.conflitos())
        inst = processo.iniciar(
            f"Aprovar {self.numero} ({self.get_tipo_display()}, risco {self.get_risco_display().lower()}): "
            f"{self.titulo}"[:200], usuario,
            f"Área: {self.area}\nJanela: {timezone.localtime(self.janela_inicio):%d/%m/%Y %H:%M} a "
            f"{timezone.localtime(self.janela_fim):%d/%m/%Y %H:%M}\n\nImpacto: {self.impacto}\n\n"
            f"Retorno: {self.plano_retorno}{aviso}", objeto=self)
        self._mudar("em_aprovacao")
        return inst

    def workflow_pular(self, etapa):
        # Emergencial vai direto ao CAB; sem gestor cadastrado, a etapa do gestor é pulada.
        return "gestor" in etapa.nome.lower() and (self.tipo == "emergencial" or not self.area.gestor_id)

    def workflow_responsavel(self, etapa):
        return self.area.gestor if "gestor" in etapa.nome.lower() else None

    def workflow_aprovado(self, instancia):
        self._mudar("aprovada")
        notificar([self.responsavel], f"{self.numero} aprovada. Janela: "
                                      f"{timezone.localtime(self.janela_inicio):%d/%m %H:%M}.",
                  reverse("servicedesk:mudanca_detail", args=[self.pk]), email=True)

    def workflow_recusado(self, instancia, comentario):
        self._mudar("recusada", comentario)
        notificar([self.responsavel, self.criado_por], f"{self.numero} recusada: {comentario}",
                  reverse("servicedesk:mudanca_detail", args=[self.pk]))

    def workflow_cancelado(self, instancia, comentario):
        self._mudar("rascunho")

    def iniciar_execucao(self, usuario):
        if self.status != "aprovada":
            raise ValidationError("Só mudanças aprovadas podem ser executadas.")
        self.status, self.executada_em = "execucao", timezone.now()
        self.save(update_fields=["status", "executada_em"])

    @transaction.atomic
    def concluir(self, usuario, sucesso, resultado):
        if self.status != "execucao":
            raise ValidationError("Inicie a execução antes de concluir.")
        if not resultado.strip():
            raise ValidationError("Descreva o resultado.")
        self._mudar("concluida" if sucesso else "falhou", resultado)
        texto = (f"Mudança {self.numero} {'concluída' if sucesso else 'falhou e foi revertida'}: {resultado}")
        for c in self.chamados.exclude(status__in=("fechado", "cancelado", "recusado")):
            c.mensagens.create(autor=usuario, texto=texto, sistema=True)
            notificar([c.solicitante], f"{c.numero}: {texto[:150]}", c.url())
