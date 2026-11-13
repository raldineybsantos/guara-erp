"""Manutenção: equipamentos, planos preventivos e ordens de serviço com peças do estoque."""

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import Departamento, ModeloBase
from apps.core.numeracao import NumeradoMixin


class Equipamento(ModeloBase):
    codigo = models.CharField("código / patrimônio", max_length=30, unique=True)
    nome = models.CharField(max_length=150)
    tipo = models.CharField(max_length=80, blank=True, help_text="Ex.: compressor, empilhadeira, ar-condicionado")
    fabricante = models.CharField(max_length=80, blank=True)
    modelo = models.CharField(max_length=80, blank=True)
    numero_serie = models.CharField("número de série", max_length=80, blank=True)
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.PROTECT)
    localizacao = models.CharField("localização", max_length=120, blank=True)
    data_aquisicao = models.DateField("data de aquisição", null=True, blank=True)
    critico = models.BooleanField("crítico", default=False, help_text="Parada afeta a produção ou a segurança.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "equipamento"
        verbose_name_plural = "equipamentos"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} - {self.nome}"

    def corretivas(self):
        return self.ordens.filter(tipo="corretiva", status="concluida")

    def horas_parado(self):
        """Soma do tempo entre abertura e conclusão das corretivas (indicador de indisponibilidade)."""
        total = sum(((o.conclusao - o.abertura).total_seconds() for o in self.corretivas() if o.conclusao), 0)
        return round(total / 3600, 1)
    horas_parado.short_description = "horas parado (corretivas)"


class PlanoManutencao(ModeloBase):
    TIPOS = [("preventiva", "Preventiva"), ("preditiva", "Preditiva"), ("inspecao", "Inspeção / lubrificação")]
    equipamento = models.ForeignKey(Equipamento, on_delete=models.CASCADE, related_name="planos")
    descricao = models.CharField("descrição", max_length=150)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="preventiva")
    periodicidade_dias = models.PositiveIntegerField("a cada (dias)")
    proxima = models.DateField("próxima execução")
    checklist = models.TextField("roteiro / checklist", blank=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plano de manutenção"
        verbose_name_plural = "planos de manutenção"
        ordering = ["proxima"]

    def __str__(self):
        return f"{self.descricao} ({self.equipamento.codigo})"


def gerar_preventivas(antecedencia_dias=None, hoje=None):
    """Rotina diária: abre a OS de cada plano que vence dentro da antecedência e ainda não tem OS em aberto."""
    hoje = hoje or timezone.localdate()
    dias = settings.MANUTENCAO_ANTECEDENCIA_DIAS if antecedencia_dias is None else antecedencia_dias
    criadas = 0
    for plano in PlanoManutencao.objects.filter(ativo=True, equipamento__ativo=True,
                                                proxima__lte=hoje + datetime.timedelta(days=dias)):
        if plano.ordens.exclude(status__in=("concluida", "cancelada")).exists():
            continue
        OrdemServico.objects.create(
            equipamento=plano.equipamento, plano=plano, tipo=plano.tipo, prioridade="media",
            descricao=f"{plano.descricao}\n\n{plano.checklist}".strip(), responsavel=plano.responsavel,
            data_programada=plano.proxima)
        criadas += 1
    return criadas


class OrdemServico(NumeradoMixin, ModeloBase):
    PREFIXO = "OS"
    TIPOS = [("corretiva", "Corretiva")] + PlanoManutencao.TIPOS + [("melhoria", "Melhoria")]
    PRIORIDADES = [("baixa", "Baixa"), ("media", "Média"), ("alta", "Alta"), ("emergencia", "Emergência")]
    STATUS = [("aberta", "Aberta"), ("execucao", "Em execução"), ("concluida", "Concluída"),
              ("cancelada", "Cancelada")]
    equipamento = models.ForeignKey(Equipamento, on_delete=models.PROTECT, related_name="ordens")
    plano = models.ForeignKey(PlanoManutencao, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="ordens", editable=False)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="corretiva")
    prioridade = models.CharField(max_length=10, choices=PRIORIDADES, default="media")
    descricao = models.TextField("problema / serviço solicitado")
    solicitante = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", editable=False)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="técnico responsável", null=True,
                                    blank=True, on_delete=models.PROTECT, related_name="+")
    data_programada = models.DateField("programada para", null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="aberta", editable=False, db_index=True)
    abertura = models.DateTimeField(default=timezone.now, editable=False)
    inicio = models.DateTimeField("início", null=True, blank=True, editable=False)
    conclusao = models.DateTimeField("conclusão", null=True, blank=True, editable=False)
    servico_executado = models.TextField("serviço executado", blank=True)
    horas_trabalhadas = models.DecimalField("horas trabalhadas", max_digits=8, decimal_places=2, default=0)
    custo_mao_obra = models.DecimalField("custo de mão de obra / terceiros", max_digits=12, decimal_places=2,
                                         default=0)
    custo_pecas = models.DecimalField("custo das peças", max_digits=12, decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = "ordem de serviço"
        verbose_name_plural = "ordens de serviço"
        ordering = ["-numero"]

    def save(self, *args, **kwargs):
        if not self.pk and not self.solicitante_id:
            self.solicitante = usuario_atual()
        super().save(*args, **kwargs)

    @property
    def custo_total(self):
        return self.custo_mao_obra + self.custo_pecas

    def iniciar(self):
        if self.status != "aberta":
            raise ValidationError("Só ordens abertas podem ser iniciadas.")
        self.status, self.inicio = "execucao", timezone.now()
        self.save(update_fields=["status", "inicio"])

    def cancelar(self):
        if self.status in ("concluida", "cancelada"):
            raise ValidationError("Ordem já finalizada.")
        self.status = "cancelada"
        self.save(update_fields=["status"])

    @transaction.atomic
    def concluir(self, servico_executado, horas=0, custo_mao_obra=0):
        """Baixa as peças do estoque, apura o custo e reprograma o plano preventivo."""
        from apps.estoque import models as est

        if self.status not in ("aberta", "execucao"):
            raise ValidationError("Ordem já finalizada.")
        if not servico_executado.strip():
            raise ValidationError("Descreva o serviço executado.")
        custo = Decimal(0)
        for peca in self.pecas.select_related("produto"):
            mov = est.saida(peca.produto, peca.quantidade, None, "manutencao", self.numero,
                            f"{self.equipamento.codigo}", referencia=self)
            peca.custo_unitario = mov.custo_unitario
            peca.save(update_fields=["custo_unitario"])
            custo += peca.quantidade * mov.custo_unitario
        agora = timezone.now()
        self.status, self.conclusao = "concluida", agora
        self.inicio = self.inicio or agora
        self.servico_executado = servico_executado
        self.horas_trabalhadas = Decimal(str(horas or 0))
        self.custo_mao_obra = Decimal(str(custo_mao_obra or 0))
        self.custo_pecas = custo.quantize(Decimal("0.01"))
        self.save()
        if self.plano_id:
            plano = self.plano
            plano.proxima = timezone.localdate() + datetime.timedelta(days=plano.periodicidade_dias)
            plano.save(update_fields=["proxima"])


class PecaOS(models.Model):
    ordem = models.ForeignKey(OrdemServico, on_delete=models.CASCADE, related_name="pecas")
    produto = models.ForeignKey("fiscal.Produto", verbose_name="peça", on_delete=models.PROTECT,
                                limit_choices_to={"tipo": "produto"})
    quantidade = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    custo_unitario = models.DecimalField("custo unitário", max_digits=14, decimal_places=4, default=0,
                                         editable=False)

    class Meta:
        verbose_name = "peça"
        verbose_name_plural = "peças"
