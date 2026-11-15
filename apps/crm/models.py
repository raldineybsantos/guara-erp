"""CRM: funil de oportunidades (prospecção → ganha/perdida), interações e conversão em cliente e pedido."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin

ESTAGIOS = [("prospeccao", "Prospecção"), ("qualificacao", "Qualificação"), ("proposta", "Proposta"),
            ("negociacao", "Negociação"), ("ganha", "Ganha"), ("perdida", "Perdida")]
ABERTOS = ["prospeccao", "qualificacao", "proposta", "negociacao"]
PROBABILIDADE = {"prospeccao": 10, "qualificacao": 25, "proposta": 50, "negociacao": 75, "ganha": 100, "perdida": 0}


class Oportunidade(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "OPT"
    ORIGENS = [("indicacao", "Indicação"), ("site", "Site / formulário"), ("evento", "Evento"),
               ("prospeccao", "Prospecção ativa"), ("cliente", "Cliente da base"), ("outro", "Outro")]
    titulo = models.CharField("título", max_length=150)
    cliente = models.ForeignKey("core.Cliente", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="oportunidades", help_text="Em branco para prospect ainda não cliente.")
    prospect_empresa = models.CharField("empresa (prospect)", max_length=200, blank=True)
    prospect_documento = models.CharField("CNPJ/CPF (prospect)", max_length=18, blank=True)
    contato = models.CharField(max_length=120, blank=True)
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    origem = models.CharField(max_length=10, choices=ORIGENS, default="prospeccao")
    estagio = models.CharField("estágio", max_length=12, choices=ESTAGIOS, default="prospeccao", db_index=True)
    valor = models.DecimalField("valor estimado", max_digits=14, decimal_places=2, default=0)
    probabilidade = models.PositiveSmallIntegerField("probabilidade %", null=True, blank=True,
                                                     help_text="Em branco = padrão do estágio.")
    previsao_fechamento = models.DateField("previsão de fechamento", null=True, blank=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    descricao = models.TextField("necessidade / observações", blank=True)
    motivo_perda = models.CharField("motivo da perda", max_length=200, blank=True)
    fechada_em = models.DateField("fechada em", null=True, blank=True, editable=False)
    pedido_venda = models.ForeignKey("vendas.PedidoVenda", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", editable=False)

    class Meta:
        verbose_name = "oportunidade"
        verbose_name_plural = "oportunidades"
        ordering = ["-numero"]

    def __str__(self):
        return f"{self.numero} {self.titulo}"

    @property
    def conta(self):
        return str(self.cliente) if self.cliente_id else self.prospect_empresa

    @property
    def status(self):  # etiqueta colorida nas listas
        return self.get_estagio_display()

    def clean(self):
        if not self.cliente_id and not self.prospect_empresa:
            raise ValidationError("Informe o cliente ou a empresa do prospect.")

    def save(self, *args, **kwargs):
        if not self.pk and not self.responsavel_id:
            self.responsavel = usuario_atual()
        if self.probabilidade is None or self.estagio in ("ganha", "perdida"):
            self.probabilidade = PROBABILIDADE[self.estagio]
        super().save(*args, **kwargs)

    @property
    def valor_ponderado(self):
        return (self.valor * Decimal(self.probabilidade or 0) / 100).quantize(Decimal("0.01"))

    def mover(self, estagio):
        if estagio not in ABERTOS:
            raise ValidationError("Use Ganhar ou Perder para fechar a oportunidade.")
        if self.estagio not in ABERTOS:
            raise ValidationError("Oportunidade já fechada.")
        self.estagio, self.probabilidade = estagio, None
        self.save()

    @transaction.atomic
    def ganhar(self, usuario=None):
        """Fecha como ganha: cadastra o prospect como cliente (se preciso) e abre o pedido de venda em rascunho."""
        from apps.core.models import Cliente, ParceiroNegocio
        from apps.vendas.models import PedidoVenda

        if self.estagio not in ABERTOS:
            raise ValidationError("Oportunidade já fechada.")
        if not self.cliente_id:
            doc = "".join(c for c in self.prospect_documento if c.isdigit())
            parceiro = ParceiroNegocio.objects.filter(documento=doc).first() if doc else None
            self.cliente = parceiro.adicionar_papel("cliente") if parceiro else Cliente.objects.create(
                nome=self.prospect_empresa, documento=doc, contato=self.contato, email=self.email,
                telefone=self.telefone, tipo_pessoa="PF" if len(doc) == 11 else "PJ")
        self.pedido_venda = PedidoVenda.objects.create(
            cliente=self.cliente, vendedor=usuario or self.responsavel,
            observacoes=f"Oportunidade {self.numero}: {self.titulo}")
        self.estagio, self.fechada_em = "ganha", timezone.localdate()
        self.save()
        return self.pedido_venda

    def perder(self, motivo):
        if self.estagio not in ABERTOS:
            raise ValidationError("Oportunidade já fechada.")
        if not motivo.strip():
            raise ValidationError("Informe o motivo da perda.")
        self.estagio, self.motivo_perda, self.fechada_em = "perdida", motivo, timezone.localdate()
        self.save()


class Interacao(models.Model):
    TIPOS = [("ligacao", "Ligação"), ("email", "E-mail"), ("reuniao", "Reunião"), ("visita", "Visita"),
             ("whatsapp", "WhatsApp"), ("proposta", "Envio de proposta")]
    oportunidade = models.ForeignKey(Oportunidade, on_delete=models.CASCADE, related_name="interacoes")
    tipo = models.CharField(max_length=10, choices=TIPOS, default="ligacao")
    data = models.DateTimeField(default=timezone.now)
    resumo = models.TextField()
    proximo_passo = models.CharField("próximo passo", max_length=200, blank=True)
    data_proximo_passo = models.DateField("data do próximo passo", null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+",
                                editable=False)

    class Meta:
        verbose_name = "interação"
        verbose_name_plural = "interações"
        ordering = ["-data"]

    def save(self, *args, **kwargs):
        novo = self.pk is None
        if not self.usuario_id:
            self.usuario = usuario_atual()
        super().save(*args, **kwargs)
        if novo and self.proximo_passo and self.data_proximo_passo:
            # O próximo passo vira cartão no Kanban de quem cuida da oportunidade.
            from django.contrib.contenttypes.models import ContentType

            from apps.tarefas.models import Atividade

            op = self.oportunidade
            Atividade.objects.create(
                titulo=f"{op.numero}: {self.proximo_passo}"[:200], descricao=f"{op.titulo} — {op.conta}",
                responsavel=op.responsavel or self.usuario, prazo=self.data_proximo_passo, etiquetas="crm",
                content_type=ContentType.objects.get_for_model(op), object_id=op.pk)
