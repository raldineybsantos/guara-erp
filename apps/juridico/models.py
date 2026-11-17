"""Jurídico: gestão de contratos de fornecedores e clientes (vigência, renovação, aprovação e assinatura)."""
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.models import Cliente, Departamento, Fornecedor, ModeloBase, PorEmpresaMixin


def somar_meses(data, meses):
    m = data.month - 1 + meses
    ano, mes = data.year + m // 12, m % 12 + 1
    import calendar
    return datetime.date(ano, mes, min(data.day, calendar.monthrange(ano, mes)[1]))


class Contrato(ModeloBase, PorEmpresaMixin):
    TIPOS = [("fornecedor", "Com fornecedor"), ("cliente", "Com cliente"), ("outro", "Outro (parceria, locação, etc.)")]
    CATEGORIAS = [("servicos", "Prestação de serviços"), ("fornecimento", "Fornecimento"), ("locacao", "Locação"),
                  ("software", "Licença / SaaS"), ("nda", "Confidencialidade (NDA)"), ("parceria", "Parceria"),
                  ("comodato", "Comodato"), ("outro", "Outro")]
    STATUS = [("rascunho", "Rascunho"), ("em_aprovacao", "Em aprovação"), ("recusado", "Recusado"),
              ("aprovado", "Aprovado — aguardando assinatura"), ("vigente", "Vigente"),
              ("encerrado", "Encerrado"), ("rescindido", "Rescindido")]
    INDICES = [("", "Sem reajuste"), ("ipca", "IPCA"), ("igpm", "IGP-M"), ("inpc", "INPC"), ("fixo", "Percentual fixo")]

    numero = models.CharField("número", max_length=20, unique=True, editable=False)
    titulo = models.CharField("título / objeto resumido", max_length=200)
    tipo = models.CharField(max_length=12, choices=TIPOS, default="fornecedor")
    categoria = models.CharField(max_length=12, choices=CATEGORIAS, default="servicos")
    fornecedor = models.ForeignKey(Fornecedor, null=True, blank=True, on_delete=models.PROTECT, related_name="contratos")
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.PROTECT, related_name="contratos")
    outra_parte = models.CharField("outra parte", max_length=200, blank=True, help_text="Quando não for fornecedor/cliente.")
    objeto = models.TextField("objeto do contrato", blank=True)
    valor_total = models.DecimalField("valor total", max_digits=14, decimal_places=2, default=0)
    valor_mensal = models.DecimalField("valor mensal", max_digits=14, decimal_places=2, default=0)
    indice_reajuste = models.CharField("índice de reajuste", max_length=5, choices=INDICES, blank=True)
    data_assinatura = models.DateField("data de assinatura", null=True, blank=True)
    inicio_vigencia = models.DateField("início da vigência")
    fim_vigencia = models.DateField("fim da vigência", null=True, blank=True, help_text="Vazio = prazo indeterminado.")
    renovacao_automatica = models.BooleanField("renovação automática", default=False)
    prazo_renovacao_meses = models.PositiveSmallIntegerField("prazo de cada renovação (meses)", default=12)
    aviso_previo_dias = models.PositiveSmallIntegerField("aviso prévio para rescisão (dias)", default=30)
    alerta_dias = models.PositiveSmallIntegerField("alertar quantos dias antes do vencimento", default=60)
    multa_rescisoria = models.CharField("multa rescisória", max_length=200, blank=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="gestor do contrato", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="contratos_geridos")
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL)
    documento = models.ForeignKey("ged.Documento", verbose_name="documento (GED)", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="contratos")
    assinaturas_necessarias = models.PositiveSmallIntegerField(
        "assinaturas digitais necessárias", default=2,
        help_text="Quando o PDF do contrato tiver esse número de assinaturas digitais válidas, ele passa a vigente.")
    status = models.CharField(max_length=12, choices=STATUS, default="rascunho", db_index=True, editable=False)
    motivo_status = models.TextField("motivo (recusa / encerramento)", blank=True, editable=False)
    ultimo_alerta_para = models.DateField(null=True, blank=True, editable=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "contrato"
        verbose_name_plural = "contratos"
        ordering = ["fim_vigencia", "numero"]
        permissions = [("aprovar_contrato", "Pode enviar contratos para aprovação e registrar assinatura")]

    def __str__(self):
        return f"{self.numero} — {self.titulo}"

    # ---------------------------------------------------------------- dados
    @property
    def parte(self):
        return self.fornecedor or self.cliente or self.outra_parte or "—"
    parte.fget.short_description = "parte contratada"

    @property
    def solicitacoes(self):
        from django.contrib.contenttypes.models import ContentType

        from apps.workflow.models import Instancia
        return Instancia.objects.filter(content_type=ContentType.objects.get_for_model(Contrato), object_id=self.pk)

    @property
    def dias_para_vencer(self):
        if not self.fim_vigencia:
            return None
        return (self.fim_vigencia - timezone.localdate()).days

    @property
    def situacao(self):
        if self.status != "vigente":
            return self.get_status_display()
        if timezone.localdate() < self.inicio_vigencia:
            return "Vigência futura"
        d = self.dias_para_vencer
        if d is None:
            return "Vigente (indeterminado)"
        if d < 0:
            return "Vencido"
        if d <= self.alerta_dias:
            return f"Vence em {d} dia(s)"
        return "Vigente"
    situacao.fget.short_description = "situação"

    @property
    def data_limite_aviso(self):
        """Último dia para avisar a outra parte, se não quiser renovar."""
        if not self.fim_vigencia:
            return None
        return self.fim_vigencia - datetime.timedelta(days=self.aviso_previo_dias)
    data_limite_aviso.fget.short_description = "prazo para aviso de não renovação"

    @property
    def aviso_expirado(self):
        limite = self.data_limite_aviso
        return bool(limite and self.status == "vigente" and limite < timezone.localdate())

    def clean(self):
        erros = {}
        if self.fim_vigencia and self.inicio_vigencia and self.fim_vigencia < self.inicio_vigencia:
            erros["fim_vigencia"] = "O fim da vigência não pode ser anterior ao início."
        if self.tipo == "fornecedor" and not self.fornecedor_id:
            erros["fornecedor"] = "Informe o fornecedor."
        if self.tipo == "cliente" and not self.cliente_id:
            erros["cliente"] = "Informe o cliente."
        if self.tipo == "outro" and not self.outra_parte:
            erros["outra_parte"] = "Informe a outra parte."
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        if not self.numero:
            ano = timezone.localdate().year
            with transaction.atomic():
                ultimo = (Contrato.objects.select_for_update().filter(numero__startswith=f"CT-{ano}-")
                          .aggregate(m=Max("numero"))["m"])
                seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
                self.numero = f"CT-{ano}-{seq:04d}"
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    # ---------------------------------------------------------------- fluxo
    def _mudar(self, status, motivo="", usuario=None):
        anterior = self.status
        self.status, self.motivo_status = status, motivo or self.motivo_status
        self.save()
        Evento.objects.create(contrato=self, tipo="status", usuario=usuario,
                              descricao=f"{dict(self.STATUS).get(anterior)} → {self.get_status_display()}"
                                        + (f": {motivo}" if motivo else ""))

    def enviar_para_aprovacao(self, usuario):
        from apps.workflow.models import Processo

        if self.status not in ("rascunho", "recusado"):
            raise ValidationError("Só contratos em rascunho ou recusados podem ir para aprovação.")
        processo = Processo.objects.filter(codigo="aprovacao-contrato", ativo=True).first()
        if processo is None:
            raise ValidationError("Processo 'aprovacao-contrato' não encontrado. Rode `python manage.py setup_erp`.")
        inst = processo.iniciar(f"Aprovar contrato {self}"[:200], usuario,
                                f"Parte: {self.parte}\nValor total: R$ {self.valor_total}\n"
                                f"Vigência: {self.inicio_vigencia:%d/%m/%Y} a "
                                f"{self.fim_vigencia.strftime('%d/%m/%Y') if self.fim_vigencia else 'indeterminado'}",
                                objeto=self)
        self._mudar("em_aprovacao", usuario=usuario)
        return inst

    # chamados pelo workflow (apps.workflow.models.Instancia)
    def workflow_aprovado(self, instancia):
        self._mudar("aprovado", usuario=instancia.historico.last().usuario if instancia.historico.exists() else None)

    def workflow_recusado(self, instancia, comentario):
        self._mudar("recusado", comentario, usuario=instancia.historico.last().usuario)

    def workflow_cancelado(self, instancia, comentario):
        self._mudar("rascunho", "Solicitação de aprovação cancelada.")

    def assinaturas_validas(self):
        """Assinaturas digitais válidas na versão atual do documento (PDF embutidas + .p7s do sistema)."""
        from apps.ged import assinatura as ass

        versao = self.documento.versao_atual if self.documento_id else None
        if versao is None:
            return []
        with versao.arquivo.open("rb") as f:
            conteudo = f.read()
        if versao.extensao == ".pdf":
            return [r for r in ass.verificar_pdf(conteudo) if r.valido]
        resultados = []
        for a in versao.assinaturas.filter(tipo="cades"):
            with a.arquivo_p7s.open("rb") as f:
                resultados += [r for r in ass.verificar_cades(conteudo, f.read()) if r.valido]
        return resultados

    def conferir_assinaturas(self, usuario=None):
        """Se já houver as assinaturas necessárias, registra a assinatura e o contrato fica vigente."""
        if self.status != "aprovado":
            return False
        validas = self.assinaturas_validas()
        if len({(r.documento or r.titular) for r in validas}) >= self.assinaturas_necessarias:
            nomes = ", ".join(sorted({r.titular for r in validas}))
            self.registrar_assinatura(usuario, observacao=f"Assinado digitalmente por: {nomes}"[:390])
            return True
        return False

    def registrar_assinatura(self, usuario, data=None, observacao=""):
        if self.status not in ("aprovado", "vigente"):
            raise ValidationError("O contrato precisa estar aprovado para ser assinado.")
        self.data_assinatura = self.data_assinatura or data or timezone.localdate()
        if self.status == "aprovado":
            self._mudar("vigente", observacao or "Contrato assinado.", usuario)
        else:
            self.save()

    @transaction.atomic
    def renovar(self, usuario, meses=None, percentual_reajuste=Decimal("0"), descricao="", automatica=False):
        if self.status != "vigente":
            raise ValidationError("Só contratos vigentes podem ser renovados.")
        if not self.fim_vigencia:
            raise ValidationError("Contrato com prazo indeterminado não precisa de renovação.")
        meses = meses or self.prazo_renovacao_meses
        anterior_fim, anterior_valor = self.fim_vigencia, self.valor_mensal
        self.fim_vigencia = somar_meses(self.fim_vigencia, meses)
        fator = 1 + (percentual_reajuste or 0) / Decimal(100)
        self.valor_mensal = (self.valor_mensal * fator).quantize(Decimal("0.01"))
        self.valor_total = (self.valor_total * fator).quantize(Decimal("0.01"))
        self.ultimo_alerta_para = None
        self.save()
        return Aditivo.objects.create(
            contrato=self, tipo="renovacao", data=timezone.localdate(), fim_anterior=anterior_fim,
            nova_data_fim=self.fim_vigencia, valor_anterior=anterior_valor, novo_valor_mensal=self.valor_mensal,
            percentual_reajuste=percentual_reajuste or 0, criado_por=usuario,
            descricao=descricao or ("Renovação automática" if automatica else f"Renovação por {meses} meses"),
        )

    def encerrar(self, usuario, motivo, rescisao=False):
        if self.status not in ("vigente", "aprovado"):
            raise ValidationError("Só contratos vigentes ou aprovados podem ser encerrados.")
        if not motivo:
            raise ValidationError("Informe o motivo.")
        self._mudar("rescindido" if rescisao else "encerrado", motivo, usuario)


class Aditivo(ModeloBase):
    TIPOS = [("renovacao", "Renovação"), ("prazo", "Aditivo de prazo"), ("valor", "Aditivo de valor"),
             ("reajuste", "Reajuste"), ("escopo", "Aditivo de escopo")]
    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name="aditivos")
    tipo = models.CharField(max_length=10, choices=TIPOS)
    data = models.DateField(default=timezone.localdate)
    descricao = models.TextField("descrição")
    fim_anterior = models.DateField("fim anterior", null=True, blank=True)
    nova_data_fim = models.DateField("novo fim da vigência", null=True, blank=True)
    valor_anterior = models.DecimalField("valor mensal anterior", max_digits=14, decimal_places=2, null=True, blank=True)
    novo_valor_mensal = models.DecimalField("novo valor mensal", max_digits=14, decimal_places=2, null=True, blank=True)
    percentual_reajuste = models.DecimalField("reajuste %", max_digits=6, decimal_places=2, default=0)
    documento = models.ForeignKey("ged.Documento", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "aditivo / renovação"
        verbose_name_plural = "aditivos e renovações"
        ordering = ["-data", "-pk"]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.data:%d/%m/%Y}"


INDICES_ECONOMICOS = [("ipca", "IPCA"), ("igpm", "IGP-M"), ("inpc", "INPC")]


class ValorIndice(ModeloBase):
    """Variação mensal (%) de um índice econômico, digitada a partir da divulgação oficial (IBGE / FGV)."""

    indice = models.CharField("índice", max_length=5, choices=INDICES_ECONOMICOS)
    competencia = models.DateField("mês de referência", help_text="Qualquer dia do mês; fica gravado o dia 1.")
    variacao = models.DecimalField("variação no mês (%)", max_digits=8, decimal_places=4,
                                   help_text="Ex.: 0,4500 para 0,45%. Pode ser negativa (deflação).")

    class Meta:
        verbose_name = "valor de índice"
        verbose_name_plural = "valores de índices"
        ordering = ["indice", "-competencia"]
        constraints = [models.UniqueConstraint(fields=["indice", "competencia"], name="valorindice_unico")]

    def __str__(self):
        return f"{self.get_indice_display()} {self.competencia:%m/%Y}: {self.variacao}%"

    def save(self, *args, **kwargs):
        self.competencia = self.competencia.replace(day=1)
        super().save(*args, **kwargs)

    @staticmethod
    def acumulado(indice, ate, meses=12):
        """Variação acumulada (%) dos `meses` meses que terminam em `ate` (inclusive).

        Compõe os índices mensais (produtório). Se faltar algum mês, levanta ValidationError listando-os."""
        fim = ate.replace(day=1)
        competencias = [somar_meses(fim, -i) for i in range(meses - 1, -1, -1)]
        achados = {v.competencia: v.variacao for v in ValorIndice.objects.filter(indice=indice, competencia__in=competencias)}
        faltam = [c for c in competencias if c not in achados]
        if faltam:
            raise ValidationError("Faltam valores do índice para: " + ", ".join(f"{c:%m/%Y}" for c in faltam) + ".")
        fator = Decimal(1)
        for c in competencias:
            fator *= 1 + achados[c] / Decimal(100)
        return ((fator - 1) * 100).quantize(Decimal("0.01"))


def _reajustar(self, usuario, percentual, descricao="", data=None):
    """Aplica um reajuste (%) ao valor mensal e total do contrato vigente e registra o aditivo."""
    if self.status != "vigente":
        raise ValidationError("Só contratos vigentes podem ser reajustados.")
    percentual = Decimal(percentual or 0)
    if percentual <= Decimal("-100"):
        raise ValidationError("Percentual inválido.")
    with transaction.atomic():
        anterior = self.valor_mensal
        fator = 1 + percentual / Decimal(100)
        self.valor_mensal = (self.valor_mensal * fator).quantize(Decimal("0.01"))
        self.valor_total = (self.valor_total * fator).quantize(Decimal("0.01"))
        self.save()
        aditivo = Aditivo.objects.create(
            contrato=self, tipo="reajuste", data=data or timezone.localdate(), valor_anterior=anterior,
            novo_valor_mensal=self.valor_mensal, percentual_reajuste=percentual, criado_por=usuario,
            descricao=descricao or f"Reajuste de {percentual}%" + (f" pelo {self.get_indice_reajuste_display()}" if self.indice_reajuste else ""))
        Evento.objects.create(contrato=self, tipo="reajuste", usuario=usuario,
                              descricao=f"Reajuste de {percentual}%: {anterior} → {self.valor_mensal}.")
    return aditivo


def _sugestao_reajuste(self, ate=None):
    """Percentual sugerido pelo índice do contrato (acumulado de 12 meses até o mês anterior a `ate`).

    Levanta ValidationError se o contrato não usa índice oficial ou faltam meses cadastrados."""
    if self.indice_reajuste not in dict(INDICES_ECONOMICOS):
        raise ValidationError("O contrato não tem índice oficial de reajuste (IPCA, IGP-M ou INPC).")
    ate = ate or timezone.localdate()
    return ValorIndice.acumulado(self.indice_reajuste, somar_meses(ate.replace(day=1), -1))


Contrato.reajustar = _reajustar
Contrato.sugestao_reajuste = _sugestao_reajuste


class Medicao(ModeloBase):
    """Medição periódica de serviços/fornecimento de um contrato; aprovada, vira conta a pagar ou a receber."""

    STATUS = [("rascunho", "Rascunho"), ("aprovada", "Aprovada"), ("faturada", "Faturada"), ("cancelada", "Cancelada")]

    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT, related_name="medicoes")
    numero = models.PositiveIntegerField("nº da medição", editable=False)
    periodo_inicio = models.DateField("início do período")
    periodo_fim = models.DateField("fim do período")
    descricao = models.CharField("descrição / serviços medidos", max_length=250, blank=True)
    valor = models.DecimalField("valor medido", max_digits=14, decimal_places=2)
    multas = models.DecimalField("multas / glosas", max_digits=14, decimal_places=2, default=0,
                                 help_text="Descontos por descumprimento (atraso, SLA, qualidade).")
    bonificacoes = models.DecimalField("bonificações / acréscimos", max_digits=14, decimal_places=2, default=0)
    vencimento = models.DateField("vencimento do título", null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="rascunho", editable=False, db_index=True)
    data_aprovacao = models.DateField(null=True, blank=True, editable=False)
    aprovada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                     on_delete=models.SET_NULL, related_name="+")
    contapagar = models.ForeignKey("financeiro.ContaPagar", null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name="medicoes")
    contareceber = models.ForeignKey("financeiro.ContaReceber", null=True, blank=True, editable=False,
                                     on_delete=models.SET_NULL, related_name="medicoes")
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "medição"
        verbose_name_plural = "medições"
        ordering = ["contrato", "-numero"]
        constraints = [models.UniqueConstraint(fields=["contrato", "numero"], name="medicao_numero_unico")]

    def __str__(self):
        return f"{self.contrato.numero} / M{self.numero}"

    @property
    def valor_liquido(self):
        return (self.valor or 0) - (self.multas or 0) + (self.bonificacoes or 0)

    @property
    def titulo(self):
        return self.contapagar or self.contareceber

    def clean(self):
        erros = {}
        if self.periodo_inicio and self.periodo_fim and self.periodo_fim < self.periodo_inicio:
            erros["periodo_fim"] = "O fim do período não pode ser anterior ao início."
        if self.valor is not None and self.valor <= 0:
            erros["valor"] = "O valor medido deve ser positivo."
        if (self.multas or 0) < 0 or (self.bonificacoes or 0) < 0:
            erros["multas"] = "Multas e bonificações devem ser informadas como valores positivos."
        elif self.valor is not None and self.multas and self.multas > self.valor + (self.bonificacoes or 0):
            erros["multas"] = "As multas não podem superar o valor medido."
        if self.pk and self.status != "rascunho":
            erros["valor"] = "Medição já aprovada não pode ser alterada: cancele e lance outra."
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        if not self.numero:
            with transaction.atomic():
                ultimo = (Medicao.objects.select_for_update().filter(contrato_id=self.contrato_id)
                          .aggregate(m=Max("numero"))["m"] or 0)
                self.numero = ultimo + 1
                super().save(*args, **kwargs)
            return
        super().save(*args, **kwargs)

    def _medido_aprovado(self):
        qs = Medicao.objects.filter(contrato_id=self.contrato_id, status__in=["aprovada", "faturada"]).exclude(pk=self.pk)
        return qs.aggregate(t=models.Sum("valor"))["t"] or Decimal(0)

    @transaction.atomic
    def aprovar(self, usuario):
        if self.status != "rascunho":
            raise ValidationError("Só medições em rascunho podem ser aprovadas.")
        c = self.contrato
        if c.status != "vigente":
            raise ValidationError("O contrato precisa estar vigente.")
        if self.periodo_inicio < c.inicio_vigencia or (c.fim_vigencia and self.periodo_fim > c.fim_vigencia):
            raise ValidationError("O período medido está fora da vigência do contrato.")
        if c.valor_total and self._medido_aprovado() + self.valor > c.valor_total:
            raise ValidationError(
                f"Esta medição estoura o valor do contrato: já medido {self._medido_aprovado()} + {self.valor} "
                f"> {c.valor_total}. Formalize um aditivo de valor antes.")
        self.status, self.data_aprovacao, self.aprovada_por = "aprovada", timezone.localdate(), usuario
        self.save()
        Evento.objects.create(contrato=c, tipo="medicao", usuario=usuario, descricao=f"Medição {self.numero} aprovada ({self.valor_liquido}).")

    @transaction.atomic
    def gerar_titulo(self, usuario=None):
        from apps.financeiro.models import ContaPagar, ContaReceber

        if self.status != "aprovada":
            raise ValidationError("Só medições aprovadas geram título.")
        c = self.contrato
        if self.valor_liquido <= 0:
            raise ValidationError("O valor líquido da medição é zero: não há o que cobrar ou pagar.")
        dados = dict(descricao=f"Medição {self.numero} — {c.titulo}"[:200], documento=f"{c.numero}/M{self.numero}",
                     valor=self.valor_liquido, competencia=self.periodo_fim.replace(day=1),
                     vencimento=self.vencimento or self.periodo_fim + datetime.timedelta(days=30), empresa=c.empresa,
                     observacoes=f"Gerado da medição {self.numero} do contrato {c.numero}.")
        if c.tipo == "fornecedor":
            self.contapagar = ContaPagar.objects.create(fornecedor=c.fornecedor, **dados)
        elif c.tipo == "cliente":
            self.contareceber = ContaReceber.objects.create(cliente=c.cliente, **dados)
        else:
            raise ValidationError("Contratos com 'outra parte' não geram título automático.")
        self.status = "faturada"
        self.save()
        Evento.objects.create(contrato=c, tipo="medicao", usuario=usuario, descricao=f"Medição {self.numero} faturada: título {self.titulo.documento}.")
        return self.titulo

    @transaction.atomic
    def cancelar(self, usuario=None):
        if self.status == "cancelada":
            raise ValidationError("Medição já cancelada.")
        t = self.titulo
        if t is not None:
            t.cancelar()  # barra se já houver pagamento
        self.status = "cancelada"
        self.save()
        Evento.objects.create(contrato=self.contrato, tipo="medicao", usuario=usuario, descricao=f"Medição {self.numero} cancelada.")


class Caucao(ModeloBase):
    """Garantia contratual (caução em dinheiro, seguro-garantia, fiança...)."""

    TIPOS = [("dinheiro", "Caução em dinheiro"), ("seguro", "Seguro-garantia"), ("fianca", "Fiança bancária"),
             ("titulo", "Título / outra garantia")]
    STATUS = [("ativa", "Ativa"), ("devolvida", "Devolvida / liberada"), ("executada", "Executada")]

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name="caucoes")
    tipo = models.CharField(max_length=10, choices=TIPOS, default="seguro")
    valor = models.DecimalField(max_digits=14, decimal_places=2)
    emissao = models.DateField("emissão", default=timezone.localdate)
    validade = models.DateField("validade", null=True, blank=True)
    emissor = models.CharField("seguradora / banco", max_length=120, blank=True)
    apolice = models.CharField("nº da apólice / documento", max_length=60, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="ativa", editable=False)
    ultimo_alerta = models.DateField(null=True, blank=True, editable=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "caução / garantia"
        verbose_name_plural = "cauções e garantias"
        ordering = ["validade", "-pk"]

    def __str__(self):
        return f"{self.get_tipo_display()} de {self.valor} — {self.contrato.numero}"

    def clean(self):
        if self.validade and self.emissao and self.validade < self.emissao:
            raise ValidationError({"validade": "A validade não pode ser anterior à emissão."})
        if self.valor is not None and self.valor <= 0:
            raise ValidationError({"valor": "O valor deve ser positivo."})

    def liberar(self, usuario=None, executar=False):
        if self.status != "ativa":
            raise ValidationError("A garantia não está ativa.")
        self.status = "executada" if executar else "devolvida"
        self.save()
        Evento.objects.create(contrato=self.contrato, tipo="caucao", usuario=usuario,
                              descricao=f"Garantia {self.get_tipo_display()} {'executada' if executar else 'devolvida'}.")


class Paralisacao(ModeloBase):
    """Suspensão do contrato; ao retomar, pode estender o prazo pelos dias parados."""

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name="paralisacoes")
    inicio = models.DateField("início da paralisação")
    fim = models.DateField("retomada (último dia parado)", null=True, blank=True, editable=False)
    motivo = models.TextField()
    estende_prazo = models.BooleanField("estender a vigência pelos dias parados", default=True)
    dias = models.PositiveIntegerField("dias parados", default=0, editable=False)

    class Meta:
        verbose_name = "paralisação"
        verbose_name_plural = "paralisações"
        ordering = ["-inicio"]

    def __str__(self):
        return f"{self.contrato.numero}: parado desde {self.inicio:%d/%m/%Y}"

    @property
    def ativa(self):
        return self.fim is None

    def clean(self):
        if self.contrato_id and not self.pk:
            if self.contrato.status != "vigente":
                raise ValidationError("Só contratos vigentes podem ser paralisados.")
            if self.contrato.paralisacoes.filter(fim__isnull=True).exists():
                raise ValidationError("Já existe uma paralisação em aberto neste contrato.")

    @transaction.atomic
    def retomar(self, usuario, fim):
        if not self.ativa:
            raise ValidationError("Esta paralisação já foi encerrada.")
        if fim < self.inicio:
            raise ValidationError("A retomada não pode ser anterior ao início.")
        self.fim, self.dias = fim, (fim - self.inicio).days + 1
        self.save()
        c = self.contrato
        if self.estende_prazo and c.fim_vigencia:
            anterior = c.fim_vigencia
            c.fim_vigencia = anterior + datetime.timedelta(days=self.dias)
            c.ultimo_alerta_para = None
            c.save()
            Aditivo.objects.create(contrato=c, tipo="prazo", data=timezone.localdate(), fim_anterior=anterior,
                                   nova_data_fim=c.fim_vigencia, criado_por=usuario,
                                   descricao=f"Prorrogação por paralisação de {self.dias} dia(s): {self.motivo}"[:500])
        Evento.objects.create(contrato=c, tipo="paralisacao", usuario=usuario,
                              descricao=f"Contrato retomado após {self.dias} dia(s) de paralisação.")


class Evento(models.Model):
    """Linha do tempo do contrato."""

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name="eventos")
    data = models.DateTimeField(auto_now_add=True)
    tipo = models.CharField(max_length=20)
    descricao = models.CharField("descrição", max_length=400)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-data", "-pk"]
        verbose_name = "evento do contrato"
        verbose_name_plural = "eventos dos contratos"

    def __str__(self):
        return self.descricao


def processar_vencimentos(hoje=None):
    """Rotina diária: cria atividades de alerta no Kanban e renova os contratos automáticos vencidos.

    Retorna (alertas_criados, renovados)."""
    from django.contrib.auth.models import Group

    from apps.tarefas.models import Atividade

    hoje = hoje or timezone.localdate()
    alertas = renovados = 0
    juridico = Group.objects.filter(name="Jurídico").first()
    for c in Contrato.objects.filter(status="vigente", fim_vigencia__isnull=False):
        dias = (c.fim_vigencia - hoje).days
        if c.renovacao_automatica and dias < 0:
            c.renovar(None, automatica=True)
            Evento.objects.create(contrato=c, tipo="renovacao", descricao=f"Renovado automaticamente até {c.fim_vigencia:%d/%m/%Y}")
            renovados += 1
            continue
        if dias <= c.alerta_dias and c.ultimo_alerta_para != c.fim_vigencia:
            acao = "renovar ou encerrar" if not c.renovacao_automatica else "confirmar a renovação automática"
            limite = c.data_limite_aviso
            descr = (f"O contrato {c} com {c.parte} vence em {c.fim_vigencia:%d/%m/%Y} ({dias} dia(s)).\n"
                     f"Decida se vai {acao}."
                     + (f"\nPrazo para avisar a outra parte (aviso prévio): {limite:%d/%m/%Y}." if limite else ""))
            a = Atividade(titulo=f"Contrato a vencer: {c.numero} — {c.titulo}"[:200], descricao=descr,
                          prioridade="urgente" if dias <= 15 else "alta", prazo=limite if limite and limite >= hoje else c.fim_vigencia,
                          responsavel=c.responsavel, grupo=None if c.responsavel else juridico,
                          departamento=c.departamento, etiquetas="contrato, vencimento")
            a.vinculo = c
            a.save()
            c.ultimo_alerta_para = c.fim_vigencia
            c.save(update_fields=["ultimo_alerta_para"])
            Evento.objects.create(contrato=c, tipo="alerta", descricao=f"Alerta de vencimento criado no quadro de tarefas ({dias} dias).")
            alertas += 1
    # garantias a vencer
    for g in Caucao.objects.filter(status="ativa", validade__isnull=False, contrato__status="vigente").select_related("contrato"):
        c = g.contrato
        dias = (g.validade - hoje).days
        if dias <= 30 and g.ultimo_alerta != g.validade:
            a = Atividade(titulo=f"Garantia a vencer: {c.numero} — {g.get_tipo_display()}"[:200],
                          descricao=f"A garantia ({g.get_tipo_display()}, {g.valor}) do contrato {c} vence em {g.validade:%d/%m/%Y}. Renove ou substitua.",
                          prioridade="urgente" if dias <= 10 else "alta", prazo=max(g.validade, hoje),
                          responsavel=c.responsavel, grupo=None if c.responsavel else juridico,
                          departamento=c.departamento, etiquetas="contrato, garantia")
            a.vinculo = c
            a.save()
            g.ultimo_alerta = g.validade
            g.save(update_fields=["ultimo_alerta"])
            alertas += 1
    return alertas, renovados
