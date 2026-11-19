"""Plano de cargos e salários (CSA): faixas por cargo e nível, enquadramento do colaborador, movimentações
(mérito, promoção, dissídio, enquadramento) com aprovação por outra pessoa e histórico salarial."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import Colaborador, ModeloBase

D2 = Decimal("0.01")


class FaixaSalarial(ModeloBase):
    NIVEIS = [("estagio", "Estágio"), ("junior", "Júnior"), ("pleno", "Pleno"), ("senior", "Sênior"),
              ("especialista", "Especialista"), ("coordenacao", "Coordenação"), ("gerencia", "Gerência"),
              ("unico", "Nível único")]

    cargo = models.ForeignKey("core.Cargo", on_delete=models.PROTECT, related_name="faixas")
    nivel = models.CharField("nível", max_length=14, choices=NIVEIS, default="unico")
    minimo = models.DecimalField("mínimo", max_digits=12, decimal_places=2)
    maximo = models.DecimalField("máximo", max_digits=12, decimal_places=2)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "faixa salarial"
        verbose_name_plural = "faixas salariais"
        ordering = ["cargo__nome", "minimo"]
        constraints = [models.UniqueConstraint(fields=["cargo", "nivel"], name="uq_faixa_cargo_nivel")]
        permissions = [("gerir_salarios", "Pode solicitar movimentações salariais e manter o plano de cargos"),
                       ("aprovar_salarios", "Pode aprovar movimentações salariais")]

    def __str__(self):
        return f"{self.cargo} — {self.get_nivel_display()} ({self.minimo} a {self.maximo})"

    def clean(self):
        if self.minimo is not None and self.maximo is not None:
            if self.minimo <= 0:
                raise ValidationError({"minimo": "O mínimo deve ser maior que zero."})
            if self.maximo < self.minimo:
                raise ValidationError({"maximo": "O máximo não pode ser menor que o mínimo."})

    @property
    def ponto_medio(self):
        return ((self.minimo + self.maximo) / 2).quantize(D2)

    def contem(self, valor):
        return self.minimo <= valor <= self.maximo


class Enquadramento(ModeloBase):
    """Em que faixa o colaborador está hoje."""

    colaborador = models.OneToOneField("core.Colaborador", on_delete=models.CASCADE, related_name="enquadramento")
    faixa = models.ForeignKey(FaixaSalarial, on_delete=models.PROTECT, related_name="enquadrados")

    class Meta:
        verbose_name = "enquadramento"
        verbose_name_plural = "enquadramentos"
        ordering = ["colaborador__nome"]

    def __str__(self):
        return f"{self.colaborador} → {self.faixa}"

    def clean(self):
        c, f = self.colaborador if self.colaborador_id else None, self.faixa if self.faixa_id else None
        if c and f and c.cargo_id != f.cargo_id:
            raise ValidationError({"faixa": "A faixa pertence a outro cargo; o enquadramento deve seguir o cargo do colaborador."})

    @property
    def compa_ratio(self):
        pm = self.faixa.ponto_medio
        return (self.colaborador.salario / pm).quantize(D2) if pm else None

    @property
    def situacao(self):
        s, f = self.colaborador.salario, self.faixa
        return "abaixo da faixa" if s < f.minimo else "acima da faixa" if s > f.maximo else "dentro da faixa"


class MovimentoSalarial(ModeloBase):
    TIPOS = [("merito", "Mérito"), ("promocao", "Promoção"), ("dissidio", "Dissídio / reajuste coletivo"),
             ("enquadramento", "Enquadramento / correção"), ("outro", "Outro")]
    STATUS = [("pendente", "Aguardando aprovação"), ("aprovado", "Aprovado (aguarda vigência)"), ("aplicado", "Aplicado"),
              ("rejeitado", "Rejeitado"), ("cancelado", "Cancelado")]

    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="movimentos_salariais")
    tipo = models.CharField(max_length=14, choices=TIPOS, default="merito")
    data_efetiva = models.DateField("vigência", default=timezone.localdate)
    salario_novo = models.DecimalField("novo salário", max_digits=12, decimal_places=2)
    cargo_novo = models.ForeignKey("core.Cargo", verbose_name="novo cargo (promoção)", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    faixa_nova = models.ForeignKey(FaixaSalarial, verbose_name="nova faixa", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    excecao = models.BooleanField("exceção à faixa", default=False,
                                  help_text="Marque para autorizar um salário fora da faixa; exige justificativa.")
    motivo = models.CharField("justificativa", max_length=300)
    lote = models.CharField(max_length=40, blank=True, editable=False)
    salario_anterior = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, editable=False)
    cargo_anterior = models.ForeignKey("core.Cargo", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+", editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="pendente", editable=False)
    decidido_por = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     editable=False)
    decidido_em = models.DateTimeField(null=True, blank=True, editable=False)
    parecer = models.CharField("parecer do aprovador", max_length=300, blank=True, editable=False)
    aplicado_em = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "movimentação salarial"
        verbose_name_plural = "movimentações salariais"
        ordering = ["-data_efetiva", "-id"]

    def __str__(self):
        return f"{self.colaborador} — {self.get_tipo_display()} → {self.salario_novo}"

    # ------------------------------------------------------------ validação
    def clean(self):
        if not self.colaborador_id or self.salario_novo is None:
            return
        if self.salario_novo <= 0:
            raise ValidationError({"salario_novo": "O novo salário deve ser maior que zero."})
        if self.colaborador.status == "desligado":
            raise ValidationError("O colaborador está desligado.")
        if not self.pk and self.salario_novo == self.colaborador.salario and not self.cargo_novo_id:
            raise ValidationError({"salario_novo": "O novo salário é igual ao atual."})
        if self.tipo == "promocao" and not self.cargo_novo_id and not self.faixa_nova_id:
            raise ValidationError("A promoção deve indicar o novo cargo ou a nova faixa.")
        cargo_alvo = self.cargo_novo_id or self.colaborador.cargo_id
        if self.faixa_nova_id:
            if self.faixa_nova.cargo_id != cargo_alvo:
                raise ValidationError({"faixa_nova": "A faixa não pertence ao cargo do colaborador após a movimentação."})
            if not self.faixa_nova.contem(self.salario_novo):
                if not self.excecao:
                    raise ValidationError({"salario_novo": f"O salário está fora da faixa ({self.faixa_nova.minimo} a "
                                                           f"{self.faixa_nova.maximo}). Marque exceção e justifique."})
        if self.excecao and len((self.motivo or "").strip()) < 10:
            raise ValidationError({"motivo": "A exceção exige uma justificativa de ao menos 10 caracteres."})

    # ------------------------------------------------------------ ciclo
    def _exigir(self, *status):
        if self.status not in status:
            raise ValidationError(f"A movimentação está {self.get_status_display().lower()}; ação não aplicável.")

    @transaction.atomic
    def aprovar(self, usuario, parecer=""):
        self._exigir("pendente")
        if not usuario.is_superuser and self.criado_por_id == usuario.pk:
            raise ValidationError("Quem solicitou a movimentação não pode aprová-la (segregação de funções).")
        self.status, self.decidido_por, self.decidido_em = "aprovado", usuario, timezone.now()
        self.parecer = parecer[:300]
        self.save(update_fields=["status", "decidido_por", "decidido_em", "parecer", "atualizado_em"])
        if self.data_efetiva <= timezone.localdate():
            self.aplicar()

    def rejeitar(self, usuario, parecer):
        self._exigir("pendente")
        if not parecer or not parecer.strip():
            raise ValidationError("Informe o motivo da rejeição.")
        self.status, self.decidido_por, self.decidido_em, self.parecer = "rejeitado", usuario, timezone.now(), parecer[:300]
        self.save(update_fields=["status", "decidido_por", "decidido_em", "parecer", "atualizado_em"])

    def cancelar(self):
        self._exigir("pendente", "aprovado")
        self.status = "cancelado"
        self.save(update_fields=["status", "atualizado_em"])

    @transaction.atomic
    def aplicar(self):
        """Grava o novo salário/cargo no colaborador. Chamado ao aprovar (vigência já chegou) ou pela rotina diária."""
        self._exigir("aprovado")
        c = Colaborador.objects.select_for_update().get(pk=self.colaborador_id)
        if c.status == "desligado":
            raise ValidationError("O colaborador foi desligado antes da vigência.")
        self.salario_anterior, self.cargo_anterior = c.salario, c.cargo
        c.salario = self.salario_novo
        if self.cargo_novo_id:
            c.cargo_id = self.cargo_novo_id
        c.save(update_fields=["salario", "cargo", "atualizado_em"])
        if self.faixa_nova_id:
            Enquadramento.objects.update_or_create(colaborador=c, defaults={"faixa": self.faixa_nova})
        elif self.cargo_novo_id:
            Enquadramento.objects.filter(colaborador=c).delete()    # mudou de cargo sem faixa nova: reenquadrar
        self.status, self.aplicado_em = "aplicado", timezone.localdate()
        self.save(update_fields=["salario_anterior", "cargo_anterior", "status", "aplicado_em", "atualizado_em"])

    @property
    def variacao_pct(self):
        base = self.salario_anterior if self.salario_anterior is not None else self.colaborador.salario
        return ((self.salario_novo / base - 1) * 100).quantize(D2) if base else None


@transaction.atomic
def reajuste_coletivo(percentual, data_efetiva, motivo, empresa=None, cargo=None, departamento=None, usuario=None):
    """Cria uma movimentação pendente de dissídio para cada colaborador ativo do recorte (lote único)."""
    percentual = Decimal(percentual)
    if percentual == 0 or percentual < Decimal("-50") or percentual > 100:
        raise ValidationError("Informe um percentual entre -50% e 100%, diferente de zero.")
    if not (motivo or "").strip():
        raise ValidationError("Informe a justificativa (por exemplo, a convenção coletiva).")
    qs = Colaborador.objects.exclude(status="desligado").filter(salario__gt=0, data_admissao__lte=data_efetiva)
    if empresa:
        qs = qs.filter(empresa=empresa)
    if cargo:
        qs = qs.filter(cargo=cargo)
    if departamento:
        qs = qs.filter(departamento=departamento)
    lote = f"DIS-{timezone.localdate():%Y%m%d}-{MovimentoSalarial.objects.filter(lote__startswith='DIS-').values('lote').distinct().count() + 1}"
    n = 0
    for c in qs:
        novo = (c.salario * (1 + percentual / 100)).quantize(D2)
        if novo <= 0:
            continue
        m = MovimentoSalarial(colaborador=c, tipo="dissidio", data_efetiva=data_efetiva, salario_novo=novo,
                              motivo=motivo.strip()[:300], lote=lote)
        if usuario is not None and getattr(usuario, "pk", None):
            m.criado_por = usuario
        m.save()
        n += 1
    if not n:
        raise ValidationError("Nenhum colaborador no recorte informado.")
    return lote, n


def aprovar_lote(lote, usuario, parecer=""):
    """Aprova todas as pendências do lote que o usuário puder aprovar; devolve (aprovadas, recusadas_por_regra)."""
    ok = bad = 0
    for m in MovimentoSalarial.objects.filter(lote=lote, status="pendente"):
        try:
            m.aprovar(usuario, parecer)
            ok += 1
        except ValidationError:
            bad += 1
    return ok, bad


def aplicar_vigentes(hoje=None):
    """Rotina diária: aplica as movimentações aprovadas cuja vigência chegou."""
    hoje = hoje or timezone.localdate()
    n = 0
    for m in MovimentoSalarial.objects.filter(status="aprovado", data_efetiva__lte=hoje):
        try:
            m.aplicar()
            n += 1
        except ValidationError:
            m.status = "cancelado"
            m.parecer = "Cancelada: colaborador desligado antes da vigência"
            m.save(update_fields=["status", "parecer", "atualizado_em"])
    return n


def posicao_na_faixa(empresa=None):
    """Colaboradores ativos com a situação na faixa e o compa-ratio (salário ÷ ponto médio)."""
    qs = Colaborador.objects.exclude(status="desligado").select_related("cargo", "enquadramento__faixa")
    if empresa:
        qs = qs.filter(empresa=empresa)
    linhas = []
    for c in qs.order_by("cargo__nome", "nome"):
        e = getattr(c, "enquadramento", None)
        linhas.append({"colaborador": c, "faixa": e.faixa if e else None, "compa": e.compa_ratio if e else None,
                       "situacao": e.situacao if e else "sem enquadramento"})
    return linhas
