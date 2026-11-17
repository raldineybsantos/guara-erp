import datetime
import re

from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import models
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
    # --- gateway de pagamento (boleto/PIX com baixa automática) ---
    gateway_provedor = models.CharField("gateway", max_length=20, blank=True, editable=False)
    gateway_id = models.CharField("ID no gateway", max_length=60, blank=True, db_index=True, editable=False)
    gateway_status = models.CharField("situação no gateway", max_length=30, blank=True, editable=False)
    url_boleto = models.URLField("PDF do boleto", max_length=500, blank=True, editable=False)
    url_recibo = models.URLField("comprovante", max_length=500, blank=True, editable=False)
    pix_qrcode_png = models.TextField("QR Code do PIX (base64)", blank=True, editable=False)
    pago_em = models.DateField("pago em", null=True, blank=True, editable=False)
    valor_liquido = models.DecimalField("valor líquido", max_digits=14, decimal_places=2, null=True, blank=True,
                                        editable=False, help_text="O que o gateway repassa, já descontada a tarifa.")
    taxa = models.DecimalField("tarifa do gateway", max_digits=14, decimal_places=2, default=0, editable=False)

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
        """Somente textos simples: o modelo de mensagem não tem acesso a objetos do banco."""
        emp = Empresa.padrao()
        titulo = self.conta_receber
        linhas = []
        if self.linha_digitavel:
            linhas.append(f"Linha digitável: {self.linha_digitavel}")
        if self.pix_copia_cola:
            linhas.append(f"PIX copia-e-cola: {self.pix_copia_cola}")
        if self.url_pagamento:
            linhas.append(f"Pague pelo link: {self.url_pagamento}")
        return {
            "cliente": str(self.cliente or ""),
            "titulo.descricao": titulo.descricao,
            "titulo.documento": titulo.documento,
            "valor": f"R$ {self.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "vencimento": self.vencimento.strftime("%d/%m/%Y"),
            "dias_atraso": str(max((timezone.localdate() - self.vencimento).days, 0)),
            "cobranca.linha_digitavel": self.linha_digitavel,
            "cobranca.pix_copia_cola": self.pix_copia_cola,
            "cobranca.url_pagamento": self.url_pagamento,
            "dados_pagamento": "\n".join(linhas),
            "empresa": str(emp or ""),
        }

    def enviar(self, regra=None, usuario=None):
        """Envia a cobrança por e-mail e registra no histórico."""
        assunto_tpl = regra.assunto if regra else "Cobrança {{ titulo.descricao }} - vencimento {{ vencimento }}"
        corpo_tpl = regra.mensagem if regra else MENSAGEM_PADRAO
        ctx = self.contexto_mensagem()
        assunto = renderizar_mensagem(assunto_tpl, ctx).replace("\n", " ")
        corpo = renderizar_mensagem(corpo_tpl, ctx)
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
{{ dados_pagamento }}

Atenciosamente,
{{ empresa }}"""

VARIAVEIS_MENSAGEM = ("cliente", "titulo.descricao", "titulo.documento", "valor", "vencimento", "dias_atraso",
                      "cobranca.linha_digitavel", "cobranca.pix_copia_cola", "cobranca.url_pagamento",
                      "dados_pagamento", "empresa")
_RE_VARIAVEL = re.compile(r"{{\s*([\w.]+)\s*}}")


def renderizar_mensagem(modelo, contexto):
    """Substitui {{ variavel }} por texto. NÃO usa o motor de templates do Django: assim quem edita a
    régua não consegue executar tags nem navegar em objetos (ex.: ler dados de usuários)."""
    modelo = re.sub(r"{%.*?%}", "", modelo or "", flags=re.S)  # descarta tags de modelos antigos
    return _RE_VARIAVEL.sub(lambda m: str(contexto.get(m.group(1), "")), modelo)


def validar_modelo_mensagem(texto):
    if "{%" in (texto or ""):
        raise ValidationError("Tags {% %} não são permitidas. Use apenas variáveis {{ ... }}.")
    desconhecidas = sorted(set(_RE_VARIAVEL.findall(texto or "")) - set(VARIAVEIS_MENSAGEM))
    if desconhecidas:
        raise ValidationError("Variáveis não permitidas: %s. Disponíveis: %s." % (
            ", ".join(desconhecidas), ", ".join(VARIAVEIS_MENSAGEM)))


class ReguaCobranca(ModeloBase):
    """Regra automática: quantos dias antes/depois do vencimento enviar e com qual mensagem."""

    nome = models.CharField(max_length=100)
    dias = models.IntegerField("dias em relação ao vencimento",
                               help_text="Negativo = antes do vencimento (-3), 0 = no dia, positivo = após (5).")
    assunto = models.CharField(max_length=200, default="Lembrete: {{ titulo.descricao }} vence em {{ vencimento }}",
                               validators=[validar_modelo_mensagem])
    mensagem = models.TextField(default=MENSAGEM_PADRAO, validators=[validar_modelo_mensagem],
                                help_text="Variáveis: {{ cliente }}, {{ titulo.descricao }}, {{ titulo.documento }}, "
                                          "{{ valor }}, {{ vencimento }}, {{ dias_atraso }}, {{ dados_pagamento }}, "
                                          "{{ cobranca.pix_copia_cola }}, {{ cobranca.linha_digitavel }}, "
                                          "{{ cobranca.url_pagamento }}, {{ empresa }}")
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


# --------------------------------------------------------------------------- gateway de pagamento
def _cifrar(texto):
    from apps.fiscal import cofre

    return cofre.cifrar(texto.encode())


def _decifrar(dados):
    from apps.fiscal import cofre

    return cofre.decifrar(dados).decode()


class ConfiguracaoGateway(ModeloBase):
    """Conta de gateway (Asaas ou Efí) de uma empresa: emite boleto/PIX/cartão e avisa os pagamentos por webhook.
    A chave da API e o token do webhook ficam cifrados (cofre) e fora da auditoria."""

    PROVEDORES = [("asaas", "Asaas"), ("efi", "Efí (Gerencianet)")]
    AMBIENTES = [("sandbox", "Sandbox (testes, sem dinheiro de verdade)"), ("producao", "Produção")]

    empresa = models.ForeignKey(Empresa, on_delete=models.PROTECT, related_name="gateways")
    provedor = models.CharField(max_length=20, choices=PROVEDORES, default="asaas")
    ambiente = models.CharField(max_length=10, choices=AMBIENTES, default="sandbox")
    ativo = models.BooleanField(default=True)
    client_id = models.CharField("Client_Id (Efí)", max_length=120, blank=True,
                                 help_text="Só para a Efí: Client_Id da aplicação. A chave abaixo é o Client_Secret.")
    chave_api = models.BinaryField(editable=False, default=b"")
    token_webhook = models.BinaryField(editable=False, default=b"")
    conta_bancaria = models.ForeignKey("financeiro.ContaBancaria", verbose_name="conta que recebe os repasses",
                                       on_delete=models.PROTECT, related_name="+",
                                       help_text="Os pagamentos recebidos pelo gateway dão baixa nesta conta.")
    multa_pct = models.DecimalField("multa por atraso (%)", max_digits=5, decimal_places=2, default=2)
    juros_mes_pct = models.DecimalField("juros ao mês (%)", max_digits=5, decimal_places=2, default=1)
    lancar_tarifa = models.BooleanField("lançar a tarifa do gateway como despesa", default=True,
                                        help_text="Faz o saldo da conta bater com o que o gateway realmente repassa.")

    class Meta:
        verbose_name = "gateway de pagamento"
        verbose_name_plural = "gateways de pagamento"
        constraints = [models.UniqueConstraint(fields=["empresa", "provedor"], name="gateway_unico_por_empresa")]

    def __str__(self):
        return f"{self.get_provedor_display()} — {self.empresa} ({self.get_ambiente_display().split(' ')[0]})"

    def definir_chave(self, texto):
        self.chave_api = _cifrar(texto.strip()) if texto else b""

    def obter_chave(self):
        return _decifrar(self.chave_api) if self.chave_api else ""

    def novo_token_webhook(self):
        import secrets

        token = secrets.token_urlsafe(48)           # 64 caracteres, sem espaços (o Asaas exige 32 a 255)
        self.token_webhook = _cifrar(token)
        return token

    def obter_token(self):
        return _decifrar(self.token_webhook) if self.token_webhook else ""

    def clean(self):
        from django.conf import settings

        if self.ambiente == "producao" and not getattr(settings, "COBRANCA_GATEWAY_PRODUCAO", False):
            raise ValidationError({"ambiente": "Produção está travada. Libere com COBRANCA_GATEWAY_PRODUCAO=1 no .env "
                                               "depois de validar todo o fluxo no sandbox."})
        if self.provedor == "efi" and not (self.client_id or "").strip():
            raise ValidationError({"client_id": "Informe o Client_Id da Efí."})
        for campo in ("multa_pct", "juros_mes_pct"):
            v = getattr(self, campo)
            if v is not None and not (0 <= v <= 20):
                raise ValidationError({campo: "Informe um percentual entre 0 e 20."})


class ClienteGateway(models.Model):
    """Cadastro do cliente no gateway (criado na primeira cobrança)."""

    config = models.ForeignKey(ConfiguracaoGateway, on_delete=models.CASCADE, related_name="clientes")
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="+")
    externo_id = models.CharField(max_length=60)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["config", "cliente"], name="cliente_gateway_unico")]


class EventoGateway(models.Model):
    """Cada aviso (webhook) recebido. O ID do evento é único: reenvios não processam duas vezes."""

    config = models.ForeignKey(ConfiguracaoGateway, on_delete=models.CASCADE, related_name="eventos")
    evento_id = models.CharField(max_length=200)
    tipo = models.CharField(max_length=60)
    pagamento_id = models.CharField(max_length=60, blank=True, db_index=True)
    recebido_em = models.DateTimeField(default=timezone.now)
    resultado = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["config", "evento_id"], name="evento_gateway_unico")]
        ordering = ["-recebido_em"]
