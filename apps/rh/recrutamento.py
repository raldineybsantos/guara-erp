"""Recrutamento e seleção (RSP): vagas, candidatos, funil de etapas, entrevistas, proposta e contratação
(que cria o colaborador)."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import Colaborador, ModeloBase, PorEmpresaMixin, notificar
from apps.core.numeracao import NumeradoMixin
from apps.core.validators import validar_cpf

CPF_LIMPO = lambda v: "".join(ch for ch in (v or "") if ch.isdigit())  # noqa: E731


class Vaga(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "VAG"
    STATUS = [("rascunho", "Rascunho"), ("aberta", "Aberta"), ("pausada", "Pausada"), ("preenchida", "Preenchida"),
              ("cancelada", "Cancelada")]

    titulo = models.CharField("título da vaga", max_length=150)
    cargo = models.ForeignKey("core.Cargo", on_delete=models.PROTECT, related_name="vagas")
    departamento = models.ForeignKey("core.Departamento", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="vagas")
    gestor = models.ForeignKey("core.Colaborador", verbose_name="gestor da vaga", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="vagas_gestor")
    tipo_contrato = models.CharField("tipo de contrato", max_length=20, choices=Colaborador.CONTRATOS, default="clt")
    quantidade = models.PositiveIntegerField("posições", default=1)
    faixa_min = models.DecimalField("faixa salarial — mínimo", max_digits=12, decimal_places=2, default=0)
    faixa_max = models.DecimalField("faixa salarial — máximo", max_digits=12, decimal_places=2, default=0)
    descricao = models.TextField("descrição e requisitos", blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="rascunho", editable=False)
    aberta_em = models.DateField(null=True, blank=True, editable=False)
    fechada_em = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "vaga"
        verbose_name_plural = "vagas"
        ordering = ["-criado_em"]
        permissions = [("gerir_recrutamento", "Pode conduzir o recrutamento, propor e contratar")]

    def __str__(self):
        return f"{self.numero or 'Vaga'} — {self.titulo}"

    def clean(self):
        if self.quantidade is not None and self.quantidade < 1:
            raise ValidationError({"quantidade": "A vaga precisa de ao menos uma posição."})
        if self.faixa_max and self.faixa_min and self.faixa_max < self.faixa_min:
            raise ValidationError({"faixa_max": "O máximo da faixa não pode ser menor que o mínimo."})

    @property
    def contratados(self):
        return self.candidaturas.filter(etapa="contratado").count()

    @property
    def posicoes_livres(self):
        return max(self.quantidade - self.contratados, 0)

    def _exigir(self, *status):
        if self.status not in status:
            raise ValidationError(f"A vaga está {self.get_status_display().lower()}; essa ação não se aplica agora.")

    def abrir(self):
        self._exigir("rascunho", "pausada")
        self.status = "aberta"
        self.aberta_em = self.aberta_em or timezone.localdate()
        self.save(update_fields=["status", "aberta_em", "atualizado_em"])

    def pausar(self):
        self._exigir("aberta")
        self.status = "pausada"
        self.save(update_fields=["status", "atualizado_em"])

    def cancelar(self):
        self._exigir("rascunho", "aberta", "pausada")
        self.status, self.fechada_em = "cancelada", timezone.localdate()
        self.save(update_fields=["status", "fechada_em", "atualizado_em"])
        self.candidaturas.exclude(etapa__in=Candidatura.ENCERRADAS).update(
            etapa="reprovado", motivo="Vaga cancelada")


class Candidato(ModeloBase):
    ORIGENS = [("site", "Site / portal"), ("indicacao", "Indicação"), ("rede", "Rede profissional"),
               ("agencia", "Agência"), ("banco", "Banco de talentos"), ("outra", "Outra")]

    nome = models.CharField(max_length=200)
    cpf = models.CharField("CPF", max_length=14, blank=True, validators=[validar_cpf])
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    origem = models.CharField(max_length=10, choices=ORIGENS, default="site")
    pretensao = models.DecimalField("pretensão salarial", max_digits=12, decimal_places=2, default=0)
    curriculo = models.TextField("resumo do currículo / link", blank=True)
    consentimento_lgpd = models.BooleanField("consentiu o uso dos dados no processo seletivo (LGPD)", default=False)
    banco_talentos = models.BooleanField("manter no banco de talentos", default=False)

    class Meta:
        verbose_name = "candidato"
        verbose_name_plural = "candidatos"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def clean(self):
        if not self.email and not self.telefone:
            raise ValidationError("Informe ao menos um contato: e-mail ou telefone.")


class Candidatura(ModeloBase):
    ETAPAS = [("triagem", "Triagem"), ("entrevista_rh", "Entrevista com RH"), ("entrevista_gestor", "Entrevista com gestor"),
              ("teste", "Teste / avaliação técnica"), ("proposta", "Proposta"), ("contratado", "Contratado"),
              ("reprovado", "Reprovado"), ("desistiu", "Desistiu")]
    FUNIL = ["triagem", "entrevista_rh", "entrevista_gestor", "teste", "proposta"]
    ENCERRADAS = ("contratado", "reprovado", "desistiu")
    PROPOSTAS = [("", "—"), ("enviada", "Enviada"), ("aceita", "Aceita"), ("recusada", "Recusada")]

    vaga = models.ForeignKey(Vaga, on_delete=models.PROTECT, related_name="candidaturas")
    candidato = models.ForeignKey(Candidato, on_delete=models.PROTECT, related_name="candidaturas")
    etapa = models.CharField(max_length=18, choices=ETAPAS, default="triagem", editable=False)
    motivo = models.CharField("motivo da reprovação / desistência", max_length=300, blank=True, editable=False)
    proposta_salario = models.DecimalField("salário proposto", max_digits=12, decimal_places=2, null=True, blank=True,
                                           editable=False)
    proposta_inicio = models.DateField("início previsto", null=True, blank=True, editable=False)
    proposta_status = models.CharField(max_length=10, choices=PROPOSTAS, blank=True, default="", editable=False)
    colaborador = models.OneToOneField("core.Colaborador", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="candidatura_origem", editable=False)

    class Meta:
        verbose_name = "candidatura"
        verbose_name_plural = "candidaturas"
        ordering = ["vaga", "candidato__nome"]
        constraints = [models.UniqueConstraint(fields=["vaga", "candidato"], name="uq_candidatura_vaga_candidato")]

    def __str__(self):
        return f"{self.candidato} → {self.vaga.titulo}"

    def clean(self):
        if self.vaga_id and self.candidato_id and not self.pk:
            if self.vaga.status not in ("aberta", "pausada", "rascunho"):
                raise ValidationError("A vaga não aceita mais candidaturas.")
            if not self.candidato.consentimento_lgpd:
                raise ValidationError("O candidato precisa ter consentido o uso dos dados (LGPD) para entrar no processo.")

    def _registrar(self, de, para, usuario=None, obs=""):
        HistoricoEtapa.objects.create(candidatura=self, de=de, para=para, observacao=obs[:300],
                                      usuario=usuario if getattr(usuario, "pk", None) else None)

    def _aberta(self):
        if self.etapa in self.ENCERRADAS:
            raise ValidationError(f"A candidatura já está {self.get_etapa_display().lower()}.")

    @transaction.atomic
    def mover(self, etapa, usuario=None, obs=""):
        self._aberta()
        if etapa not in self.FUNIL:
            raise ValidationError("Etapa inválida para movimentação.")
        if self.vaga.status not in ("aberta",):
            raise ValidationError("A vaga precisa estar aberta para movimentar candidatos.")
        if etapa == "proposta":
            raise ValidationError("Use “Enviar proposta” para levar o candidato à etapa de proposta.")
        if self.FUNIL.index(etapa) < self.FUNIL.index(self.etapa) and self.etapa == "proposta":
            raise ValidationError("Com proposta enviada, só é possível reprovar, desistir ou contratar.")
        antes, self.etapa = self.etapa, etapa
        self.save(update_fields=["etapa", "atualizado_em"])
        self._registrar(antes, etapa, usuario, obs)

    @transaction.atomic
    def reprovar(self, motivo, usuario=None, desistencia=False):
        self._aberta()
        if not motivo or not motivo.strip():
            raise ValidationError("Informe o motivo.")
        antes = self.etapa
        self.etapa = "desistiu" if desistencia else "reprovado"
        self.motivo = motivo.strip()[:300]
        if self.proposta_status == "enviada":
            self.proposta_status = "recusada"
        self.save(update_fields=["etapa", "motivo", "proposta_status", "atualizado_em"])
        self._registrar(antes, self.etapa, usuario, self.motivo)

    @transaction.atomic
    def enviar_proposta(self, salario, inicio, usuario=None):
        self._aberta()
        if self.vaga.status != "aberta":
            raise ValidationError("A vaga precisa estar aberta para enviar propostas.")
        salario = Decimal(salario or 0)
        if salario <= 0:
            raise ValidationError("Informe o salário proposto.")
        v = self.vaga
        if (v.faixa_max and salario > v.faixa_max) or (v.faixa_min and salario < v.faixa_min):
            raise ValidationError(f"O salário está fora da faixa da vaga ({v.faixa_min} a {v.faixa_max}).")
        if not inicio or inicio < timezone.localdate():
            raise ValidationError("Informe uma data de início que não esteja no passado.")
        antes = self.etapa
        self.etapa, self.proposta_salario, self.proposta_inicio, self.proposta_status = "proposta", salario, inicio, "enviada"
        self.save(update_fields=["etapa", "proposta_salario", "proposta_inicio", "proposta_status", "atualizado_em"])
        self._registrar(antes, "proposta", usuario, f"Proposta de {salario}")

    def responder_proposta(self, aceita, usuario=None, motivo=""):
        if self.etapa != "proposta" or self.proposta_status != "enviada":
            raise ValidationError("Não há proposta aguardando resposta.")
        if aceita:
            self.proposta_status = "aceita"
            self.save(update_fields=["proposta_status", "atualizado_em"])
            self._registrar("proposta", "proposta", usuario, "Proposta aceita")
        else:
            self.reprovar(motivo or "Proposta recusada pelo candidato", usuario, desistencia=True)

    @transaction.atomic
    def contratar(self, cpf=None, usuario=None):
        """Cria o colaborador a partir da proposta aceita. O CPF é exigido (o candidato pode não ter informado)."""
        c = Candidatura.objects.select_for_update().select_related("vaga", "candidato").get(pk=self.pk)
        if c.etapa != "proposta" or c.proposta_status != "aceita":
            raise ValidationError("A contratação exige uma proposta aceita.")
        v = c.vaga
        if v.posicoes_livres <= 0:
            raise ValidationError("Todas as posições da vaga já foram preenchidas.")
        cpf = CPF_LIMPO(cpf or c.candidato.cpf)
        if not cpf:
            raise ValidationError("Informe o CPF do candidato para contratar.")
        validar_cpf(cpf)
        if Colaborador.objects.filter(cpf=cpf).exists():
            raise ValidationError("Já existe um colaborador com esse CPF.")
        colab = Colaborador(
            empresa=v.empresa, nome=c.candidato.nome, cpf=cpf, email=c.candidato.email, telefone=c.candidato.telefone,
            departamento=v.departamento, cargo=v.cargo, gestor=v.gestor, tipo_contrato=v.tipo_contrato,
            data_admissao=c.proposta_inicio, salario=c.proposta_salario)
        colab.save()
        c.colaborador, c.etapa = colab, "contratado"
        c.save(update_fields=["colaborador", "etapa", "atualizado_em"])
        c._registrar("proposta", "contratado", usuario, f"Colaborador {colab.nome} criado")
        if cpf != c.candidato.cpf:
            c.candidato.cpf = cpf
            c.candidato.save(update_fields=["cpf", "atualizado_em"])
        if v.posicoes_livres == 0:
            v.status, v.fechada_em = "preenchida", timezone.localdate()
            v.save(update_fields=["status", "fechada_em", "atualizado_em"])
            outros = v.candidaturas.exclude(pk=c.pk).exclude(etapa__in=Candidatura.ENCERRADAS)
            for o in outros:
                o.reprovar("Vaga preenchida", usuario)
        self.etapa, self.colaborador = c.etapa, colab
        return colab


class HistoricoEtapa(models.Model):
    candidatura = models.ForeignKey(Candidatura, on_delete=models.CASCADE, related_name="historico")
    de = models.CharField(max_length=18, blank=True)
    para = models.CharField(max_length=18)
    observacao = models.CharField(max_length=300, blank=True)
    usuario = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    em = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = "histórico de etapa"
        verbose_name_plural = "histórico de etapas"
        ordering = ["em", "id"]

    def __str__(self):
        return f"{self.de} → {self.para}"


class Entrevista(ModeloBase):
    TIPOS = [("rh", "RH"), ("gestor", "Gestor"), ("tecnica", "Técnica"), ("painel", "Painel")]

    candidatura = models.ForeignKey(Candidatura, on_delete=models.CASCADE, related_name="entrevistas")
    tipo = models.CharField(max_length=8, choices=TIPOS, default="rh")
    data_hora = models.DateTimeField("data e hora", default=timezone.now)
    entrevistador = models.ForeignKey("core.Colaborador", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="entrevistas_feitas")
    nota = models.PositiveIntegerField("nota (1 a 5)", null=True, blank=True)
    parecer = models.TextField(blank=True)

    class Meta:
        verbose_name = "entrevista"
        verbose_name_plural = "entrevistas"
        ordering = ["data_hora"]

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.candidatura}"

    def clean(self):
        if self.nota is not None and not 1 <= self.nota <= 5:
            raise ValidationError({"nota": "A nota vai de 1 a 5."})
        if self.candidatura_id and self.candidatura.etapa in Candidatura.ENCERRADAS and not self.pk:
            raise ValidationError("A candidatura já está encerrada.")
