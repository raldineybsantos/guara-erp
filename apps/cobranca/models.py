import datetime

from django.core.mail import send_mail
from django.db import models
from django.template import Context, Template
from django.utils import timezone

from apps.core.models import Cliente, Empresa, ModeloBase
from apps.financeiro.models import ContaReceber

from .pix import gerar_pix


class Cobranca(ModeloBase):
    TIPOS = [("pix", "PIX"), ("boleto", "Boleto"), ("link", "Link de pagamento"), ("cartao", "Cartão")]
    STATUS = [("gerada", "Gerada"), ("enviada", "Enviada"), ("paga", "Paga"), ("cancelada", "Cancelada")]

    conta_receber = models.ForeignKey(ContaReceber, verbose_name="conta a receber", on_delete=models.CASCADE,
                                      related_name="cobrancas")
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="cobrancas", null=True, blank=True)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="pix")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    vencimento = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="gerada", db_index=True)
    nosso_numero = models.CharField("nosso número", max_length=30, blank=True)
    linha_digitavel = models.CharField("linha digitável", max_length=60, blank=True,
                                       help_text="Preencha com o retorno do banco/gateway.")
    pix_copia_cola = models.TextField("PIX copia-e-cola", blank=True)
    url_pagamento = models.URLField("link de pagamento", blank=True)
    data_envio = models.DateTimeField("último envio", null=True, blank=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "cobrança"
        verbose_name_plural = "cobranças"
        ordering = ["vencimento", "pk"]

    def __str__(self):
        return f"Cobrança #{self.pk} - {self.cliente or ''} - {self.vencimento:%d/%m/%Y}"

    @property
    def email_destino(self):
        if not self.cliente:
            return ""
        return self.cliente.email_cobranca or self.cliente.email

    def save(self, *args, **kwargs):
        if not self.cliente_id and self.conta_receber_id:
            self.cliente = self.conta_receber.cliente
        if self.tipo == "pix" and not self.pix_copia_cola:
            emp = self.conta_receber.empresa or Empresa.padrao()
            if emp and emp.chave_pix:
                self.pix_copia_cola = gerar_pix(emp.chave_pix, emp.razao_social, emp.cidade or "SAO PAULO",
                                                self.valor, txid=f"ERP{self.conta_receber_id}",
                                                descricao=self.conta_receber.descricao)
        if self.conta_receber_id and self.conta_receber.status == "pago":
            self.status = "paga"
        super().save(*args, **kwargs)

    def contexto_mensagem(self):
        emp = Empresa.padrao()
        return {"cobranca": self, "cliente": self.cliente, "titulo": self.conta_receber, "empresa": emp,
                "valor": f"R$ {self.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "vencimento": self.vencimento.strftime("%d/%m/%Y"),
                "dias_atraso": max((timezone.localdate() - self.vencimento).days, 0)}

    def enviar(self, regra=None, usuario=None):
        """Envia a cobrança por e-mail e registra no histórico."""
        assunto_tpl = regra.assunto if regra else "Cobrança {{ titulo.descricao }} - vencimento {{ vencimento }}"
        corpo_tpl = regra.mensagem if regra else MENSAGEM_PADRAO
        ctx = Context(self.contexto_mensagem())
        assunto = Template(assunto_tpl).render(ctx)
        corpo = Template(corpo_tpl).render(ctx)
        destino = self.email_destino
        sucesso, erro = False, ""
        if not destino:
            erro = "Cliente sem e-mail cadastrado."
        else:
            try:
                send_mail(assunto, corpo, None, [destino])
                sucesso = True
            except Exception as exc:  # falha de SMTP não deve derrubar a régua inteira
                erro = str(exc)[:300]
        HistoricoCobranca.objects.create(cobranca=self, regra=regra, canal="email", destino=destino,
                                         assunto=assunto[:200], mensagem=corpo, sucesso=sucesso, erro=erro)
        if sucesso:
            self.data_envio = timezone.now()
            if self.status == "gerada":
                self.status = "enviada"
            self.save()
        return sucesso


MENSAGEM_PADRAO = """Olá {{ cliente }},

Segue a cobrança referente a "{{ titulo.descricao }}".
Valor: {{ valor }}
Vencimento: {{ vencimento }}
{% if cobranca.linha_digitavel %}Linha digitável: {{ cobranca.linha_digitavel }}
{% endif %}{% if cobranca.pix_copia_cola %}PIX copia-e-cola: {{ cobranca.pix_copia_cola }}
{% endif %}{% if cobranca.url_pagamento %}Pague pelo link: {{ cobranca.url_pagamento }}
{% endif %}
Atenciosamente,
{{ empresa }}"""


class ReguaCobranca(ModeloBase):
    """Regra automática: quantos dias antes/depois do vencimento enviar e com qual mensagem."""

    nome = models.CharField(max_length=100)
    dias = models.IntegerField("dias em relação ao vencimento",
                               help_text="Negativo = antes do vencimento (-3), 0 = no dia, positivo = após (5).")
    assunto = models.CharField(max_length=200, default="Lembrete: {{ titulo.descricao }} vence em {{ vencimento }}")
    mensagem = models.TextField(default=MENSAGEM_PADRAO,
                                help_text="Variáveis: {{ cliente }}, {{ titulo.descricao }}, {{ valor }}, "
                                          "{{ vencimento }}, {{ dias_atraso }}, {{ cobranca.pix_copia_cola }}, "
                                          "{{ cobranca.linha_digitavel }}, {{ empresa }}")
    ativa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "régua de cobrança"
        verbose_name_plural = "régua de cobrança"
        ordering = ["dias"]

    def __str__(self):
        if self.dias < 0:
            quando = f"{-self.dias} dia(s) antes"
        elif self.dias == 0:
            quando = "no vencimento"
        else:
            quando = f"{self.dias} dia(s) após"
        return f"{self.nome} ({quando})"

    @classmethod
    def executar(cls, data=None):
        """Envia as cobranças que se encaixam em cada regra ativa na data (padrão: hoje)."""
        data = data or timezone.localdate()
        enviados = 0
        for regra in cls.objects.filter(ativa=True):
            alvo = data - datetime.timedelta(days=regra.dias)
            qs = (Cobranca.objects.filter(vencimento=alvo, status__in=["gerada", "enviada"])
                  .exclude(historico__regra=regra))
            for cob in qs.select_related("cliente", "conta_receber"):
                if cob.conta_receber.status in ("pago", "cancelado"):
                    continue
                enviados += int(cob.enviar(regra))
        return enviados


class HistoricoCobranca(models.Model):
    cobranca = models.ForeignKey(Cobranca, on_delete=models.CASCADE, related_name="historico")
    regra = models.ForeignKey(ReguaCobranca, null=True, blank=True, on_delete=models.SET_NULL)
    data = models.DateTimeField(auto_now_add=True)
    canal = models.CharField(max_length=20, default="email")
    destino = models.CharField(max_length=200, blank=True)
    assunto = models.CharField(max_length=200, blank=True)
    mensagem = models.TextField(blank=True)
    sucesso = models.BooleanField(default=False)
    erro = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "histórico de cobrança"
        verbose_name_plural = "histórico de cobranças"
        ordering = ["-data"]

    def __str__(self):
        return f"{timezone.localtime(self.data):%d/%m/%Y %H:%M} {self.destino}"
