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
    return alertas, renovados
