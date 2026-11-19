"""Treinamentos corporativos (TRM): cursos, turmas, inscrição, presença e nota, certificação com validade
e controle de reciclagem dos cursos obrigatórios por cargo."""
import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import Colaborador, ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin


def somar_meses(data, meses):
    m = data.month - 1 + meses
    ano, mes = data.year + m // 12, m % 12 + 1
    return datetime.date(ano, mes, min(data.day, calendar.monthrange(ano, mes)[1]))


class Curso(ModeloBase):
    CATEGORIAS = [("tecnico", "Técnico"), ("comportamental", "Comportamental"), ("seguranca", "Segurança"),
                  ("compliance", "Compliance / conduta"), ("integracao", "Integração"), ("outro", "Outro")]
    MODALIDADES = [("presencial", "Presencial"), ("online", "Online"), ("hibrido", "Híbrido")]

    nome = models.CharField(max_length=150, unique=True)
    categoria = models.CharField(max_length=15, choices=CATEGORIAS, default="tecnico")
    modalidade = models.CharField(max_length=10, choices=MODALIDADES, default="presencial")
    carga_horaria = models.DecimalField("carga horária (h)", max_digits=6, decimal_places=1, default=0)
    validade_meses = models.PositiveIntegerField("validade do certificado (meses)", default=0,
                                                 help_text="0 = não vence. Passado o prazo, o colaborador precisa reciclar.")
    frequencia_minima = models.PositiveIntegerField("frequência mínima (%)", default=75)
    nota_minima = models.DecimalField("nota mínima", max_digits=5, decimal_places=2, default=0,
                                      help_text="0 = o curso não tem avaliação.")
    cargos_obrigatorios = models.ManyToManyField("core.Cargo", verbose_name="obrigatório para os cargos", blank=True,
                                                 related_name="cursos_obrigatorios")
    descricao = models.TextField("descrição / conteúdo", blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "curso"
        verbose_name_plural = "cursos"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def clean(self):
        if self.frequencia_minima > 100:
            raise ValidationError({"frequencia_minima": "A frequência mínima é de 0 a 100%."})


class Turma(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "TRM"
    STATUS = [("planejada", "Planejada"), ("andamento", "Em andamento"), ("concluida", "Concluída"),
              ("cancelada", "Cancelada")]

    curso = models.ForeignKey(Curso, on_delete=models.PROTECT, related_name="turmas")
    instrutor = models.CharField(max_length=150, blank=True)
    fornecedor = models.CharField("instituição / fornecedor", max_length=150, blank=True)
    local = models.CharField(max_length=150, blank=True)
    data_inicio = models.DateField("início", default=timezone.localdate)
    data_fim = models.DateField("fim", default=timezone.localdate)
    vagas = models.PositiveIntegerField(default=0, help_text="0 = sem limite.")
    custo_total = models.DecimalField("custo total (R$)", max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="planejada", editable=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "turma de treinamento"
        verbose_name_plural = "turmas de treinamento"
        ordering = ["-data_inicio", "-id"]
        permissions = [("gerir_treinamento", "Pode inscrever, registrar resultados e concluir turmas")]

    def __str__(self):
        return f"{self.numero or 'Turma'} — {self.curso}"

    def clean(self):
        if self.data_fim and self.data_inicio and self.data_fim < self.data_inicio:
            raise ValidationError({"data_fim": "O fim não pode ser antes do início."})

    # ---------------------------------------------------------------- números
    def ativas(self):
        return self.participacoes.exclude(resultado="cancelado")

    @property
    def inscritos(self):
        return self.ativas().count()

    @property
    def vagas_livres(self):
        return None if not self.vagas else max(self.vagas - self.inscritos, 0)

    @property
    def custo_por_participante(self):
        n = self.inscritos
        return (self.custo_total / n).quantize(Decimal("0.01")) if n else Decimal("0")

    # ---------------------------------------------------------------- ações
    def _exigir(self, *status):
        if self.status not in status:
            nomes = ", ".join(dict(self.STATUS)[s].lower() for s in status)
            raise ValidationError(f"Isso só vale para turma {nomes} (esta está {self.get_status_display().lower()}).")

    @transaction.atomic
    def inscrever(self, colaboradores):
        turma = Turma.objects.select_for_update().get(pk=self.pk)
        turma._exigir("planejada")
        novos = []
        for c in colaboradores:
            if c.status == "desligado":
                raise ValidationError(f"{c} está desligado e não pode ser inscrito.")
            existente = turma.participacoes.filter(colaborador=c).first()
            if existente and existente.resultado != "cancelado":
                raise ValidationError(f"{c} já está inscrito nesta turma.")
            novos.append((c, existente))
        if turma.vagas and turma.inscritos + len(novos) > turma.vagas:
            raise ValidationError(f"A turma tem {turma.vagas} vaga(s) e {turma.inscritos} já ocupada(s).")
        for c, existente in novos:
            if existente:   # reativa uma inscrição cancelada
                existente.resultado = "inscrito"
                existente.save(update_fields=["resultado"])
            else:
                Participacao.objects.create(turma=turma, colaborador=c)
        return len(novos)

    def iniciar(self):
        self._exigir("planejada")
        if not self.inscritos:
            raise ValidationError("Inscreva ao menos um colaborador antes de iniciar a turma.")
        self.status = "andamento"
        self.save(update_fields=["status", "atualizado_em"])

    @transaction.atomic
    def concluir(self):
        """Fecha a turma e define o resultado de cada inscrito (frequência e nota mínimas do curso)."""
        turma = Turma.objects.select_for_update().get(pk=self.pk)
        turma._exigir("andamento")
        for p in turma.participacoes.filter(resultado="inscrito").select_related("colaborador"):
            p.apurar()
        turma.status = "concluida"
        turma.save(update_fields=["status", "atualizado_em"])
        self.status = "concluida"

    def cancelar(self):
        self._exigir("planejada", "andamento")
        self.participacoes.exclude(resultado="cancelado").update(resultado="cancelado")
        self.status = "cancelada"
        self.save(update_fields=["status", "atualizado_em"])


class Participacao(ModeloBase):
    RESULTADOS = [("inscrito", "Inscrito"), ("aprovado", "Aprovado"), ("reprovado", "Reprovado"),
                  ("ausente", "Ausente"), ("cancelado", "Cancelado")]

    turma = models.ForeignKey(Turma, on_delete=models.CASCADE, related_name="participacoes")
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="treinamentos")
    presenca_pct = models.PositiveIntegerField("frequência (%)", null=True, blank=True)
    nota = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    resultado = models.CharField(max_length=10, choices=RESULTADOS, default="inscrito", editable=False)
    concluido_em = models.DateField("concluído em", null=True, blank=True, editable=False)
    valido_ate = models.DateField("certificado válido até", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "participação em treinamento"
        verbose_name_plural = "participações em treinamentos"
        ordering = ["-turma__data_inicio", "colaborador__nome"]
        constraints = [models.UniqueConstraint(fields=["turma", "colaborador"], name="uq_participacao_turma_colab")]

    def __str__(self):
        return f"{self.colaborador} — {self.turma}"

    def registrar(self, presenca_pct, nota):
        if self.turma.status != "andamento":
            raise ValidationError("Os resultados só são lançados com a turma em andamento.")
        if self.resultado != "inscrito":
            raise ValidationError(f"{self.colaborador}: a participação já está {self.get_resultado_display().lower()}.")
        if presenca_pct is not None and not 0 <= presenca_pct <= 100:
            raise ValidationError(f"{self.colaborador}: a frequência deve ficar entre 0 e 100%.")
        if nota is not None and nota < 0:
            raise ValidationError(f"{self.colaborador}: a nota não pode ser negativa.")
        self.presenca_pct, self.nota = presenca_pct, nota
        self.save(update_fields=["presenca_pct", "nota", "atualizado_em"])

    def apurar(self):
        curso = self.turma.curso
        if self.presenca_pct is None:
            self.resultado = "ausente"
        elif self.presenca_pct < curso.frequencia_minima or (
                curso.nota_minima > 0 and (self.nota is None or self.nota < curso.nota_minima)):
            self.resultado = "reprovado"
        else:
            self.resultado = "aprovado"
            self.concluido_em = self.turma.data_fim
            if curso.validade_meses:
                self.valido_ate = somar_meses(self.turma.data_fim, curso.validade_meses)
        self.save(update_fields=["resultado", "concluido_em", "valido_ate", "atualizado_em"])


# ------------------------------------------------------------------ obrigatoriedade e reciclagem
def pendencias(hoje=None, alerta_dias=30):
    """Colaboradores ativos sem o curso obrigatório do cargo, ou com o certificado vencido/perto de vencer."""
    hoje = hoje or timezone.localdate()
    saida = []
    for curso in Curso.objects.filter(ativo=True).exclude(cargos_obrigatorios=None).prefetch_related(
            "cargos_obrigatorios"):
        cargos = list(curso.cargos_obrigatorios.all())
        for c in Colaborador.objects.exclude(status="desligado").filter(cargo__in=cargos).select_related(
                "cargo", "departamento"):
            ultimo = (Participacao.objects.filter(colaborador=c, turma__curso=curso, resultado="aprovado")
                      .order_by("-concluido_em", "-id").first())
            if ultimo is None:
                saida.append({"colaborador": c, "curso": curso, "situacao": "pendente", "valido_ate": None, "dias": None})
            elif ultimo.valido_ate:
                dias = (ultimo.valido_ate - hoje).days
                if dias < 0:
                    saida.append({"colaborador": c, "curso": curso, "situacao": "vencido",
                                  "valido_ate": ultimo.valido_ate, "dias": dias})
                elif dias <= alerta_dias:
                    saida.append({"colaborador": c, "curso": curso, "situacao": "a_vencer",
                                  "valido_ate": ultimo.valido_ate, "dias": dias})
    ordem = {"vencido": 0, "pendente": 1, "a_vencer": 2}
    saida.sort(key=lambda x: (ordem[x["situacao"]], x["colaborador"].nome, x["curso"].nome))
    return saida


AVISAR_FALTANDO = (30, 15, 7, 1, 0)


def alertar_reciclagem(hoje=None):
    """Avisa o colaborador e o RH quando o certificado de curso obrigatório está para vencer (ou venceu)."""
    from django.contrib.auth.models import User
    from django.db.models import Q
    from django.urls import reverse

    from apps.core.models import notificar

    hoje = hoje or timezone.localdate()
    rh = list(User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(user_permissions__codename="gerir_treinamento") |
        Q(groups__permissions__codename="gerir_treinamento")).distinct())
    avisos = 0
    for p in pendencias(hoje, alerta_dias=max(AVISAR_FALTANDO)):
        if p["situacao"] == "pendente" or p["dias"] not in AVISAR_FALTANDO and p["dias"] >= 0:
            continue
        if p["dias"] < 0 and (hoje - p["valido_ate"]).days % 30 != 1:   # vencido: lembra de 30 em 30 dias
            continue
        c, curso = p["colaborador"], p["curso"]
        texto = (f"Treinamento {curso} de {c} vence em {p['dias']} dia(s) ({p['valido_ate']:%d/%m/%Y})."
                 if p["dias"] >= 0 else f"Treinamento {curso} de {c} está vencido desde {p['valido_ate']:%d/%m/%Y}.")
        notificar(rh + ([c.usuario] if c.usuario else []), texto, url=reverse("rh:pendencias_treinamento"))
        avisos += 1
    return avisos
