"""Contencioso: processos judiciais e administrativos, envolvidos, andamentos, prazos e provisão de contingência."""
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import ModeloBase, PorEmpresaMixin


class Processo(ModeloBase, PorEmpresaMixin):
    TIPOS = [("judicial", "Judicial"), ("administrativo", "Administrativo")]
    NATUREZAS = [("trabalhista", "Trabalhista"), ("civel", "Cível"), ("tributario", "Tributário"),
                 ("consumidor", "Consumidor"), ("ambiental", "Ambiental"), ("regulatorio", "Regulatório"),
                 ("societario", "Societário"), ("outro", "Outro")]
    POLOS = [("reu", "Réu / requerido"), ("autor", "Autor / requerente"), ("interessado", "Interessado")]
    RISCOS = [("remoto", "Remoto"), ("possivel", "Possível"), ("provavel", "Provável")]
    STATUS = [("andamento", "Em andamento"), ("suspenso", "Suspenso"), ("encerrado", "Encerrado")]
    RESULTADOS = [("procedente", "Procedente"), ("parcial", "Parcialmente procedente"),
                  ("improcedente", "Improcedente"), ("acordo", "Acordo"), ("extinto", "Extinto sem mérito")]

    numero = models.CharField("número do processo", max_length=40, help_text="Número CNJ ou do órgão.")
    tipo = models.CharField(max_length=14, choices=TIPOS, default="judicial")
    natureza = models.CharField(max_length=12, choices=NATUREZAS, default="civel")
    assunto = models.CharField(max_length=200)
    orgao = models.CharField("tribunal / órgão", max_length=120, blank=True)
    comarca = models.CharField("comarca / instância", max_length=120, blank=True)
    polo = models.CharField("posição da empresa", max_length=12, choices=POLOS, default="reu")
    contraparte = models.CharField(max_length=200)
    documento_contraparte = models.CharField("CPF/CNPJ da contraparte", max_length=18, blank=True)
    advogado = models.CharField("advogado / escritório responsável", max_length=200, blank=True)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável interno", null=True,
                                    blank=True, on_delete=models.SET_NULL, related_name="processos_geridos")
    contrato = models.ForeignKey("juridico.Contrato", verbose_name="contrato relacionado", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="processos")
    data_distribuicao = models.DateField("data de distribuição", default=timezone.localdate)
    valor_causa = models.DecimalField("valor da causa", max_digits=14, decimal_places=2, default=0)
    risco = models.CharField("risco de perda", max_length=8, choices=RISCOS, default="possivel", editable=False)
    valor_risco = models.DecimalField("valor estimado da perda", max_digits=14, decimal_places=2, default=0,
                                      editable=False)
    provisao = models.DecimalField("provisão constituída", max_digits=14, decimal_places=2, default=0,
                                   editable=False)
    status = models.CharField(max_length=10, choices=STATUS, default="andamento", db_index=True, editable=False)
    resultado = models.CharField(max_length=12, choices=RESULTADOS, blank=True, editable=False)
    data_encerramento = models.DateField("data de encerramento", null=True, blank=True, editable=False)
    valor_condenacao = models.DecimalField("valor pago/recebido no encerramento", max_digits=14,
                                           decimal_places=2, default=0, editable=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "processo"
        verbose_name_plural = "processos"
        ordering = ["-data_distribuicao", "-pk"]
        constraints = [models.UniqueConstraint(fields=["numero"], name="processo_numero_unico")]

    def __str__(self):
        return f"{self.numero} — {self.assunto}"

    # ------------------------------------------------------------------ prazos e andamentos
    @property
    def prazos_pendentes(self):
        return self.prazos.filter(status="pendente")

    @property
    def proximo_prazo(self):
        return self.prazos_pendentes.order_by("data_fatal").first()

    def registrar(self, descricao, tipo="outro", data=None, usuario=None):
        return Andamento.objects.create(processo=self, data=data or timezone.localdate(), tipo=tipo,
                                        descricao=descricao, registrado_por=usuario)

    # ------------------------------------------------------------------ provisão de contingência
    @transaction.atomic
    def avaliar_risco(self, risco, valor_estimado, usuario=None, data=None, motivo=""):
        """Reavalia o risco de perda. Só o risco *provável* é provisionado (CPC 25): o ajuste em relação à
        provisão já constituída vira despesa (constituição) ou reversão no razão."""
        from apps.contabilidade import servicos

        if self.status == "encerrado":
            raise ValidationError("Processo encerrado: não há mais o que reavaliar.")
        valor = Decimal(valor_estimado or 0)
        if valor < 0:
            raise ValidationError("O valor estimado não pode ser negativo.")
        data = data or timezone.localdate()
        necessaria = valor if risco == "provavel" else Decimal("0")
        ajuste = necessaria - self.provisao
        if ajuste:
            evento = "contingencia_constituicao" if ajuste > 0 else "contingencia_reversao"
            servicos.lancar(evento, abs(ajuste), data, f"Contingência do processo {self.numero}",
                            Reavaliacao.criar(self, risco, valor, ajuste, usuario, data, motivo),
                            empresa=self.empresa)
        else:
            Reavaliacao.criar(self, risco, valor, ajuste, usuario, data, motivo)
        self.risco, self.valor_risco, self.provisao = risco, valor, necessaria
        self.save()
        self.registrar(f"Risco reavaliado: {dict(self.RISCOS)[risco]}, perda estimada {valor}. {motivo}".strip(),
                       "outro", data, usuario)

    @transaction.atomic
    def encerrar(self, resultado, usuario=None, data=None, valor_condenacao=0, observacao=""):
        """Encerra o processo: reverte a provisão e, se houve condenação a pagar, abre a conta a pagar."""
        from apps.financeiro.models import ContaPagar

        if self.status == "encerrado":
            raise ValidationError("Processo já encerrado.")
        if resultado not in dict(self.RESULTADOS):
            raise ValidationError("Informe o resultado.")
        data = data or timezone.localdate()
        valor = Decimal(valor_condenacao or 0)
        if valor < 0:
            raise ValidationError("O valor não pode ser negativo.")
        if self.provisao:
            self.avaliar_risco("remoto", 0, usuario, data, "Encerramento do processo.")
            self.refresh_from_db()
        if valor > 0 and self.polo != "autor":
            ContaPagar.objects.create(
                empresa=self.empresa, descricao=f"Condenação/acordo — processo {self.numero}"[:200],
                documento=self.numero[:60], valor=valor, vencimento=data + datetime.timedelta(days=15),
                observacoes=f"{self.assunto} — {self.contraparte}")
        self.status, self.resultado, self.data_encerramento, self.valor_condenacao = "encerrado", resultado, data, valor
        self.save()
        self.registrar(f"Processo encerrado: {dict(self.RESULTADOS)[resultado]}. {observacao}".strip(), "decisao",
                       data, usuario)

    def suspender(self, usuario=None, motivo=""):
        if self.status != "andamento":
            raise ValidationError("Só processo em andamento pode ser suspenso.")
        self.status = "suspenso"
        self.save()
        self.registrar(f"Processo suspenso. {motivo}".strip(), "decisao", None, usuario)

    def retomar(self, usuario=None):
        if self.status != "suspenso":
            raise ValidationError("O processo não está suspenso.")
        self.status = "andamento"
        self.save()
        self.registrar("Processo retomado.", "outro", None, usuario)


class Reavaliacao(models.Model):
    """Histórico das reavaliações de risco e dos ajustes de provisão de cada processo."""

    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="reavaliacoes")
    data = models.DateField()
    risco = models.CharField(max_length=8, choices=Processo.RISCOS)
    valor_estimado = models.DecimalField("perda estimada", max_digits=14, decimal_places=2)
    ajuste = models.DecimalField("ajuste da provisão", max_digits=14, decimal_places=2)
    motivo = models.CharField(max_length=300, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")

    class Meta:
        verbose_name = "reavaliação de risco"
        verbose_name_plural = "reavaliações de risco"
        ordering = ["-data", "-pk"]

    @classmethod
    def criar(cls, processo, risco, valor, ajuste, usuario, data, motivo):
        return cls.objects.create(processo=processo, data=data, risco=risco, valor_estimado=valor, ajuste=ajuste,
                                  usuario=usuario, motivo=motivo[:300])

    def __str__(self):
        return f"{self.processo.numero} em {self.data:%d/%m/%Y}: {self.get_risco_display()}"



class Envolvido(ModeloBase):
    PAPEIS = [("autor", "Autor"), ("reu", "Réu"), ("advogado", "Advogado"), ("testemunha", "Testemunha"),
              ("perito", "Perito"), ("juiz", "Juiz / relator"), ("terceiro", "Terceiro interessado")]
    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="envolvidos")
    nome = models.CharField(max_length=200)
    papel = models.CharField(max_length=12, choices=PAPEIS, default="terceiro")
    documento = models.CharField("CPF/CNPJ/OAB", max_length=30, blank=True)
    contato = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "envolvido"
        verbose_name_plural = "envolvidos"
        ordering = ["processo", "papel", "nome"]

    def __str__(self):
        return f"{self.nome} ({self.get_papel_display()})"


class Andamento(ModeloBase):
    TIPOS = [("peticao", "Petição"), ("despacho", "Despacho"), ("decisao", "Decisão / sentença"),
             ("audiencia", "Audiência"), ("intimacao", "Intimação"), ("recurso", "Recurso"), ("outro", "Outro")]
    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="andamentos")
    data = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="outro")
    descricao = models.TextField("descrição")
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+", editable=False)

    class Meta:
        verbose_name = "andamento"
        verbose_name_plural = "andamentos"
        ordering = ["-data", "-pk"]

    def __str__(self):
        return f"{self.processo.numero} — {self.get_tipo_display()} em {self.data:%d/%m/%Y}"


class Prazo(ModeloBase):
    STATUS = [("pendente", "Pendente"), ("cumprido", "Cumprido"), ("perdido", "Perdido"), ("cancelado", "Cancelado")]
    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="prazos")
    descricao = models.CharField("descrição", max_length=200, help_text="Ex.: contestação, recurso, audiência.")
    data_fatal = models.DateField("data fatal")
    alertar_dias = models.PositiveSmallIntegerField("avisar quantos dias antes", default=5)
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="prazos_processuais")
    status = models.CharField(max_length=10, choices=STATUS, default="pendente", editable=False)
    ultimo_alerta = models.DateField(null=True, blank=True, editable=False)
    observacoes = models.CharField("observações", max_length=300, blank=True)

    class Meta:
        verbose_name = "prazo processual"
        verbose_name_plural = "prazos processuais"
        ordering = ["data_fatal", "pk"]

    def __str__(self):
        return f"{self.descricao} até {self.data_fatal:%d/%m/%Y}"

    @property
    def dias(self):
        return (self.data_fatal - timezone.localdate()).days

    @property
    def vencido(self):
        return self.status == "pendente" and self.data_fatal < timezone.localdate()

    def _fechar(self, status, usuario, texto):
        if self.status != "pendente":
            raise ValidationError("O prazo já foi encerrado.")
        self.status = status
        self.save()
        self.processo.registrar(f"{texto}: {self.descricao}", "outro", None, usuario)

    def cumprir(self, usuario=None):
        self._fechar("cumprido", usuario, "Prazo cumprido")

    def cancelar(self, usuario=None):
        self._fechar("cancelado", usuario, "Prazo cancelado")

    def marcar_perdido(self, usuario=None):
        self._fechar("perdido", usuario, "Prazo perdido")


def alertar_prazos(hoje=None):
    """Rotina diária: abre uma atividade no Kanban para cada prazo processual próximo ou vencido."""
    from django.contrib.auth.models import Group

    from apps.tarefas.models import Atividade

    hoje = hoje or timezone.localdate()
    juridico = Group.objects.filter(name="Jurídico").first()
    n = 0
    for p in Prazo.objects.filter(status="pendente", processo__status="andamento").select_related(
            "processo", "responsavel", "processo__responsavel"):
        dias = (p.data_fatal - hoje).days
        if dias > p.alertar_dias or p.ultimo_alerta == p.data_fatal:
            continue
        resp = p.responsavel or p.processo.responsavel
        situacao = f"vence em {p.data_fatal:%d/%m/%Y} ({dias} dia(s))" if dias >= 0 else f"VENCEU em {p.data_fatal:%d/%m/%Y}"
        a = Atividade(titulo=f"Prazo processual: {p.descricao} — {p.processo.numero}"[:200],
                      descricao=f"Processo {p.processo} ({p.processo.contraparte}): {p.descricao} {situacao}.",
                      prioridade="urgente" if dias <= 2 else "alta", prazo=max(p.data_fatal, hoje),
                      responsavel=resp, grupo=None if resp else juridico, etiquetas="processo, prazo")
        a.vinculo = p.processo
        a.save()
        p.ultimo_alerta = p.data_fatal
        p.save(update_fields=["ultimo_alerta"])
        n += 1
    return n
