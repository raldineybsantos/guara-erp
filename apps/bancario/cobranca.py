"""Remessa de cobrança (CNAB 240) para títulos a receber e processamento do retorno com baixa automática."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase
from apps.core.numeracao import NumeradoMixin
from apps.financeiro.models import ContaBancaria, ContaReceber

from . import cnab_cobranca


class RemessaCobranca(NumeradoMixin, ModeloBase):
    PREFIXO = "RCB"
    STATUS = [("aberta", "Aberta"), ("gerada", "Arquivo gerado"), ("processada", "Processada"),
              ("cancelada", "Cancelada")]

    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta de cobrança", on_delete=models.PROTECT,
                                       related_name="remessas_cobranca")
    status = models.CharField(max_length=10, choices=STATUS, default="aberta", editable=False)
    sequencial = models.PositiveIntegerField("sequencial do arquivo", null=True, blank=True, editable=False)
    gerada_em = models.DateTimeField("arquivo gerado em", null=True, blank=True, editable=False)
    gerada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)

    class Meta:
        verbose_name = "remessa de cobrança"
        verbose_name_plural = "remessas de cobrança (CNAB)"
        ordering = ["-numero"]
        permissions = [("gerar_remessa_cobranca", "Pode gerar o arquivo de remessa de cobrança")]

    @property
    def total(self):
        return sum((i.conta_receber.saldo for i in self.itens.select_related("conta_receber")), Decimal(0))

    @staticmethod
    def elegiveis():
        """Contas a receber em aberto que ainda não estão numa cobrança registrada em andamento."""
        presas = ItemCobranca.objects.filter(status__in=("pendente", "registrado"),
                                             remessa__status__in=("aberta", "gerada", "processada")
                                             ).values("conta_receber_id")
        return (ContaReceber.objects.em_aberto().exclude(pk__in=presas).select_related("cliente")
                .order_by("vencimento"))

    @transaction.atomic
    def adicionar(self, contas):
        if self.status != "aberta":
            raise ValidationError("Só remessas abertas aceitam títulos.")
        conta = ContaBancaria.objects.select_for_update().get(pk=self.conta_bancaria_id)
        ok = set(self.elegiveis().values_list("pk", flat=True))
        for c in contas:
            if c.pk not in ok:
                raise ValidationError(f"{c} não está em aberto ou já está em outra cobrança.")
            conta.nosso_numero_seq += 1
            ItemCobranca.objects.create(remessa=self, conta_receber=c, nosso_numero=f"{conta.nosso_numero_seq:010d}")
        conta.save(update_fields=["nosso_numero_seq", "atualizado_em"])

    @transaction.atomic
    def gerar_arquivo(self, usuario=None):
        if self.status not in ("aberta", "gerada"):
            raise ValidationError("Remessa já processada ou cancelada.")
        if not self.sequencial:
            ultimo = (RemessaCobranca.objects.select_for_update().filter(conta_bancaria=self.conta_bancaria)
                      .aggregate(m=models.Max("sequencial"))["m"]) or 0
            self.sequencial = ultimo + 1
        texto = cnab_cobranca.gerar_remessa(self, self.sequencial)
        self.status, self.gerada_em, self.gerada_por = "gerada", timezone.now(), usuario or usuario_atual()
        self.save()
        return texto

    def cancelar(self):
        if self.itens.filter(status__in=("registrado", "liquidado")).exists():
            raise ValidationError("O banco já registrou ou liquidou títulos desta remessa.")
        self.status = "cancelada"
        self.save(update_fields=["status", "atualizado_em"])
        self.itens.update(status="cancelado")

    def atualizar_status(self):
        if self.status == "gerada" and not self.itens.filter(status__in=("pendente", "registrado")).exists():
            self.status = "processada"
            self.save(update_fields=["status", "atualizado_em"])


class ItemCobranca(models.Model):
    STATUS = [("pendente", "Aguardando o banco"), ("registrado", "Registrado no banco"),
              ("liquidado", "Liquidado"), ("rejeitado", "Rejeitado"), ("baixado", "Baixado pelo banco"),
              ("cancelado", "Cancelado")]
    remessa = models.ForeignKey(RemessaCobranca, on_delete=models.CASCADE, related_name="itens")
    conta_receber = models.ForeignKey(ContaReceber, on_delete=models.PROTECT, related_name="itens_cobranca")
    nosso_numero = models.CharField("nosso número", max_length=20)
    status = models.CharField(max_length=10, choices=STATUS, default="pendente")
    ocorrencia = models.CharField("retorno do banco", max_length=120, blank=True)

    class Meta:
        verbose_name = "título da remessa"
        verbose_name_plural = "títulos da remessa"
        ordering = ["id"]

    @property
    def seu_numero(self):
        return f"RC{self.pk}"


@transaction.atomic
def processar_retorno_cobranca(texto):
    """Liquidação (06/17): baixa o título pelo valor pago (até o saldo). Entrada confirmada (02): registrado.
    Rejeitada (03): libera o título para nova remessa. Baixa (09): o banco baixou o título (sem pagamento).
    Reprocessar o mesmo arquivo não baixa duas vezes."""
    resumo = {"liquidados": 0, "registrados": 0, "rejeitados": 0, "baixados": 0, "ignorados": 0, "detalhes": []}
    for r in cnab_cobranca.ler_retorno(texto):
        pk = r["seu_numero"][2:] if r["seu_numero"].startswith("RC") else ""
        item = None
        if pk.isdigit():
            item = ItemCobranca.objects.select_related("remessa", "conta_receber").filter(pk=pk).first()
        if item is None or item.status in ("liquidado", "cancelado"):
            resumo["ignorados"] += 1
            continue
        mov = r["movimento"]
        oc = f"{mov} {cnab_cobranca.OCORRENCIAS.get(mov, 'Ocorrência do banco')}"
        cr = item.conta_receber
        if mov in ("06", "17"):
            valor = r["valor_pago"] or r["valor_titulo"]
            if cr.status not in ("pago", "cancelado", "renegociado") and cr.saldo > 0:
                baixa = min(valor, cr.saldo)
                cr.baixar(baixa, r["data_credito"] or timezone.localdate(), item.remessa.conta_bancaria, "boleto",
                          f"Retorno CNAB {item.remessa.numero}")
                if valor > baixa:
                    resumo["detalhes"].append(f"{cr}: pago {valor}, maior que o saldo; a diferença não foi lançada.")
            item.status = "liquidado"
            resumo["liquidados"] += 1
        elif mov == "02":
            item.status = "registrado"
            resumo["registrados"] += 1
        elif mov in ("03", "27"):
            item.status = "rejeitado"
            resumo["rejeitados"] += 1
            resumo["detalhes"].append(f"{cr}: {oc}")
        elif mov == "09":
            item.status = "baixado"
            resumo["baixados"] += 1
        else:
            resumo["ignorados"] += 1
            continue
        item.ocorrencia = oc
        item.save(update_fields=["status", "ocorrencia"])
        item.remessa.atualizar_status()
    return resumo
