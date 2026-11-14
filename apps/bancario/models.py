"""Bancos: conciliação do extrato (OFX) e borderô de pagamentos a fornecedores com remessa/retorno CNAB 240."""

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase
from apps.core.numeracao import NumeradoMixin
from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber, Movimentacao

from . import cnab


# --------------------------------------------------------------------------- extrato e conciliação
class Extrato(ModeloBase):
    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta bancária", on_delete=models.PROTECT,
                                       related_name="extratos")
    arquivo = models.CharField(max_length=150)
    inicio = models.DateField("de")
    fim = models.DateField("até")

    class Meta:
        verbose_name = "extrato importado"
        verbose_name_plural = "conciliação bancária"
        ordering = ["-fim", "-id"]

    def __str__(self):
        return f"{self.conta_bancaria} {self.inicio:%d/%m} a {self.fim:%d/%m/%Y}"

    def pendentes(self):
        return self.linhas.filter(movimentacao__isnull=True, ignorada=False).count()
    pendentes.short_description = "pendentes"

    @property
    def status(self):
        n = self.pendentes()
        return "Conciliado" if n == 0 else f"Pendente: {n} linha(s)"

    @classmethod
    @transaction.atomic
    def importar(cls, conta, nome, lancamentos):
        """Cria o extrato só com as linhas ainda não importadas (FITID) e concilia o que der sozinho."""
        ja = set(LinhaExtrato.objects.filter(conta_bancaria=conta).values_list("fitid", flat=True))
        novos = [l for l in lancamentos if l["fitid"] not in ja]
        if not novos:
            raise ValidationError("Todos os lançamentos deste arquivo já foram importados.")
        ext = cls.objects.create(conta_bancaria=conta, arquivo=nome[:150], inicio=min(l["data"] for l in novos),
                                 fim=max(l["data"] for l in novos))
        vistos = set()
        for l in novos:
            if l["fitid"] in vistos:
                continue
            vistos.add(l["fitid"])
            LinhaExtrato.objects.create(extrato=ext, conta_bancaria=conta, **l)
        ext.conciliar()
        return ext

    def conciliar(self):
        """Liga cada linha à movimentação do ERP de mesmo valor e sentido, com data até N dias de distância."""
        tolerancia = datetime.timedelta(days=settings.CONCILIACAO_TOLERANCIA_DIAS)
        n = 0
        for linha in self.linhas.filter(movimentacao__isnull=True, ignorada=False):
            candidatas = Movimentacao.objects.filter(
                conta_bancaria=self.conta_bancaria, conciliado=False, linha_extrato__isnull=True,
                tipo="entrada" if linha.valor > 0 else "saida", valor=abs(linha.valor),
                data__range=(linha.data - tolerancia, linha.data + tolerancia))
            mov = min(candidatas, key=lambda m: abs((m.data - linha.data).days), default=None)
            if mov:
                linha.vincular(mov)
                n += 1
        return n

    def so_no_erp(self):
        """Movimentações do período que o banco não mostrou (cheque não compensado, lançamento errado...)."""
        return Movimentacao.objects.filter(conta_bancaria=self.conta_bancaria, conciliado=False,
                                           data__range=(self.inicio, self.fim))


class LinhaExtrato(models.Model):
    extrato = models.ForeignKey(Extrato, on_delete=models.CASCADE, related_name="linhas")
    conta_bancaria = models.ForeignKey(ContaBancaria, on_delete=models.PROTECT, related_name="+")
    fitid = models.CharField("identificador do banco", max_length=120)
    data = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    movimentacao = models.OneToOneField(Movimentacao, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="linha_extrato")
    ignorada = models.BooleanField(default=False)

    class Meta:
        verbose_name = "linha do extrato"
        verbose_name_plural = "linhas do extrato"
        ordering = ["data", "id"]
        constraints = [models.UniqueConstraint(fields=["conta_bancaria", "fitid"], name="linha_extrato_fitid")]

    def vincular(self, mov):
        self.movimentacao = mov
        self.save(update_fields=["movimentacao"])
        Movimentacao.objects.filter(pk=mov.pk).update(conciliado=True)

    def titulos_sugeridos(self):
        """Títulos em aberto cujo saldo é exatamente o valor da linha."""
        modelo = ContaReceber if self.valor > 0 else ContaPagar
        return [t for t in modelo.objects.em_aberto().order_by("vencimento")[:500] if t.saldo == abs(self.valor)][:5]

    @transaction.atomic
    def baixar_titulo(self, titulo):
        if self.movimentacao_id:
            raise ValidationError("Linha já conciliada.")
        mov = titulo.baixar(abs(self.valor), self.data, self.conta_bancaria, observacao=f"Extrato: {self.descricao}")
        self.vincular(mov)

    @transaction.atomic
    def lancar_avulsa(self, descricao, plano_conta=None):
        if self.movimentacao_id:
            raise ValidationError("Linha já conciliada.")
        mov = Movimentacao.objects.create(
            conta_bancaria=self.conta_bancaria, empresa=self.conta_bancaria.empresa, data=self.data,
            tipo="entrada" if self.valor > 0 else "saida", valor=abs(self.valor),
            descricao=(descricao or self.descricao or "Lançamento do extrato")[:200], plano_conta=plano_conta)
        self.vincular(mov)


# --------------------------------------------------------------------------- borderô de pagamentos
class Bordero(NumeradoMixin, ModeloBase):
    PREFIXO = "BOR"
    STATUS = [("aberto", "Aberto"), ("remetido", "Remessa gerada"), ("processado", "Processado"),
              ("cancelado", "Cancelado")]
    conta_bancaria = models.ForeignKey(ContaBancaria, verbose_name="conta pagadora", on_delete=models.PROTECT,
                                       related_name="borderos")
    data_pagamento = models.DateField("data de pagamento")
    status = models.CharField(max_length=10, choices=STATUS, default="aberto", editable=False)
    sequencial = models.PositiveIntegerField("sequencial do arquivo", null=True, blank=True, editable=False)
    remetido_em = models.DateTimeField("remessa gerada em", null=True, blank=True, editable=False)
    remetido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", editable=False)

    class Meta:
        verbose_name = "borderô de pagamentos"
        verbose_name_plural = "borderôs de pagamentos (CNAB)"
        ordering = ["-numero"]
        permissions = [("gerar_remessa_bordero", "Pode gerar o arquivo de remessa ao banco")]

    @property
    def total(self):
        return self.itens.aggregate(t=models.Sum("valor"))["t"] or Decimal(0)

    @staticmethod
    def elegiveis():
        """Contas a pagar em aberto, aprovadas para pagamento e fora de outro borderô em andamento."""
        presas = ItemBordero.objects.filter(status__in=("pendente", "agendado"),
                                            bordero__status__in=("aberto", "remetido")).values("conta_pagar_id")
        return (ContaPagar.objects.em_aberto().filter(aprovado=True).exclude(pk__in=presas)
                .select_related("fornecedor").order_by("vencimento"))

    @transaction.atomic
    def adicionar(self, contas):
        if self.status != "aberto":
            raise ValidationError("Só borderôs abertos aceitam títulos.")
        ok = set(self.elegiveis().values_list("pk", flat=True))
        for c in contas:
            if c.pk not in ok:
                raise ValidationError(f"{c} não está aprovado para pagamento, já foi pago ou está em outro borderô.")
            ItemBordero.objects.create(bordero=self, conta_pagar=c, valor=c.saldo,
                                       forma="titulo" if c.codigo_barras.strip() else "credito")

    @transaction.atomic
    def gerar_remessa(self, usuario=None):
        if self.status not in ("aberto", "remetido"):
            raise ValidationError("Borderô já processado ou cancelado.")
        if self.data_pagamento < timezone.localdate():
            raise ValidationError("A data de pagamento já passou. Ajuste antes de gerar a remessa.")
        if not self.sequencial:
            ultimo = (Bordero.objects.select_for_update().filter(conta_bancaria=self.conta_bancaria)
                      .aggregate(m=models.Max("sequencial"))["m"]) or 0
            self.sequencial = ultimo + 1
        texto = cnab.gerar_remessa(self, self.sequencial)
        self.status, self.remetido_em, self.remetido_por = "remetido", timezone.now(), usuario or usuario_atual()
        self.save()
        return texto

    def cancelar(self):
        if self.itens.filter(status__in=("agendado", "pago")).exists():
            raise ValidationError("O banco já agendou ou pagou títulos deste borderô.")
        self.status = "cancelado"
        self.save(update_fields=["status"])
        self.itens.update(status="cancelado")

    def atualizar_status(self):
        if self.status == "remetido" and not self.itens.filter(status__in=("pendente", "agendado")).exists():
            self.status = "processado"
            self.save(update_fields=["status"])


class ItemBordero(models.Model):
    FORMAS = [("credito", "Crédito em conta (TED)"), ("titulo", "Boleto (código de barras)")]
    STATUS = [("pendente", "Pendente"), ("agendado", "Agendado no banco"), ("pago", "Pago"),
              ("rejeitado", "Rejeitado"), ("cancelado", "Cancelado")]
    bordero = models.ForeignKey(Bordero, on_delete=models.CASCADE, related_name="itens")
    conta_pagar = models.ForeignKey(ContaPagar, on_delete=models.PROTECT, related_name="itens_bordero")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    forma = models.CharField(max_length=8, choices=FORMAS)
    status = models.CharField(max_length=10, choices=STATUS, default="pendente")
    ocorrencia = models.CharField("retorno do banco", max_length=120, blank=True)

    class Meta:
        verbose_name = "título do borderô"
        verbose_name_plural = "títulos do borderô"
        ordering = ["id"]

    @property
    def seu_numero(self):
        return f"IB{self.pk}"


@transaction.atomic
def processar_retorno(texto):
    """Aplica o retorno do banco: pago -> baixa o título (e já nasce conciliável); rejeitado -> libera o título
    para outro borderô. Devolve um resumo. Reprocessar o mesmo arquivo não baixa duas vezes."""
    resumo = {"pagos": 0, "agendados": 0, "rejeitados": 0, "ignorados": 0, "detalhes": []}
    for r in cnab.ler_retorno(texto):
        pk = r["seu_numero"][2:] if r["seu_numero"].startswith("IB") else ""
        item = None
        if pk.isdigit():
            item = ItemBordero.objects.select_related("bordero", "conta_pagar").filter(pk=pk).first()
        if item is None or item.status in ("pago", "cancelado"):
            resumo["ignorados"] += 1
            continue
        cod = r["ocorrencia"]
        texto_oc = f"{cod} {cnab.OCORRENCIAS.get(cod, 'Ocorrência do banco')}"
        if cod == "00":
            cp = item.conta_pagar
            if cp.status not in ("pago", "cancelado"):
                cp.baixar(min(r["valor"] or item.valor, cp.saldo), r["data"] or item.bordero.data_pagamento,
                          item.bordero.conta_bancaria, "ted" if item.forma == "credito" else "boleto",
                          f"Retorno CNAB {item.bordero.numero}")
            item.status = "pago"
            resumo["pagos"] += 1
        elif cod == "BD":
            item.status = "agendado"
            resumo["agendados"] += 1
        else:
            item.status = "rejeitado"
            resumo["rejeitados"] += 1
            resumo["detalhes"].append(f"{item.conta_pagar}: {texto_oc}")
        item.ocorrencia = texto_oc
        item.save(update_fields=["status", "ocorrencia"])
        item.bordero.atualizar_status()
    return resumo
