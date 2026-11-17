"""SST / EHS: incidentes e acidentes, avaliação de riscos, entrega de EPI, ASO e treinamentos obrigatórios.

Privacidade (LGPD): ASO guarda só a aptidão (apto/inapto/restrição), nunca diagnóstico ou CID. ASOs e
treinamentos ficam restritos ao SESMT e ao RH.
"""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import Colaborador, Departamento, ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin


def _tarefa_sesmt(obj, titulo, descricao, prioridade, prazo):
    from django.contrib.auth.models import Group
    from django.contrib.contenttypes.models import ContentType

    from apps.tarefas.models import Atividade

    ct = ContentType.objects.get_for_model(obj)
    if Atividade.objects.filter(content_type=ct, object_id=obj.pk).exclude(
            status__in=("concluida", "cancelada")).exists():
        return None
    return Atividade.objects.create(titulo=titulo[:200], descricao=descricao, prioridade=prioridade, prazo=prazo,
                                    grupo=Group.objects.filter(name="SESMT").first(), etiquetas="sst",
                                    content_type=ct, object_id=obj.pk)


class Incidente(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "INC"
    TIPOS = [("quase", "Quase acidente / condição insegura"), ("sem_afastamento", "Acidente sem afastamento"),
             ("com_afastamento", "Acidente com afastamento"), ("trajeto", "Acidente de trajeto"),
             ("ambiental", "Ocorrência ambiental"), ("material", "Dano material")]
    GRAVIDADES = [("leve", "Leve"), ("moderada", "Moderada"), ("grave", "Grave"), ("fatal", "Fatal")]
    STATUS = [("aberto", "Aberto"), ("investigacao", "Em investigação"), ("acao", "Ações em andamento"),
              ("encerrado", "Encerrado")]
    data_hora = models.DateTimeField("data e hora", default=timezone.now)
    tipo = models.CharField(max_length=16, choices=TIPOS)
    gravidade = models.CharField(max_length=8, choices=GRAVIDADES, default="leve")
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.PROTECT)
    local = models.CharField(max_length=150)
    descricao = models.TextField("o que aconteceu")
    envolvidos = models.ManyToManyField(Colaborador, blank=True, related_name="+")
    causas = models.TextField("causas (investigação)", blank=True)
    acoes = models.TextField("ações corretivas e preventivas", blank=True)
    cat_emitida = models.BooleanField("CAT emitida", default=False,
                                      help_text="Acidente de trabalho: a CAT deve ser emitida até o 1º dia útil "
                                                "seguinte (imediatamente em caso de óbito).")
    numero_cat = models.CharField("número da CAT", max_length=30, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="aberto", db_index=True)

    class Meta:
        verbose_name = "incidente / acidente"
        verbose_name_plural = "incidentes e acidentes"
        ordering = ["-data_hora"]
        permissions = [("ver_todos_incidente", "Pode ver todos os incidentes")]

    @property
    def exige_cat(self):
        return self.tipo in ("sem_afastamento", "com_afastamento", "trajeto")

    def clean(self):
        if self.status == "encerrado":
            if not self.causas.strip() or not self.acoes.strip():
                raise ValidationError("Para encerrar, registre as causas e as ações.")
            if self.exige_cat and not self.cat_emitida:
                raise ValidationError("Acidente de trabalho: registre a emissão da CAT antes de encerrar.")

    def save(self, *args, **kwargs):
        novo = self.pk is None
        super().save(*args, **kwargs)
        if novo:
            urgente = self.exige_cat or self.gravidade in ("grave", "fatal")
            _tarefa_sesmt(self, f"{self.numero}: {self.get_tipo_display()} — {self.local}",
                          self.descricao + ("\n\nEmitir a CAT até o 1º dia útil seguinte." if self.exige_cat else ""),
                          "urgente" if urgente else "media",
                          timezone.localdate() + datetime.timedelta(days=1 if urgente else 7))


class AvaliacaoRisco(ModeloBase):
    TIPOS = [("fisico", "Físico"), ("quimico", "Químico"), ("biologico", "Biológico"), ("ergonomico", "Ergonômico"),
             ("acidente", "Acidente / mecânico")]
    ESCALA = [(i, str(i)) for i in range(1, 6)]
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.PROTECT)
    atividade = models.CharField("atividade / posto de trabalho", max_length=150)
    perigo = models.CharField(max_length=150, help_text="Ex.: ruído da prensa, solvente, trabalho em altura")
    tipo = models.CharField(max_length=10, choices=TIPOS)
    probabilidade = models.PositiveSmallIntegerField(choices=ESCALA, help_text="1 rara … 5 quase certa")
    severidade = models.PositiveSmallIntegerField(choices=ESCALA, help_text="1 insignificante … 5 catastrófica")
    medidas_controle = models.TextField("medidas de controle (EPC, EPI, procedimento)")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    revisar_em = models.DateField("revisar em", null=True, blank=True)

    class Meta:
        verbose_name = "avaliação de risco"
        verbose_name_plural = "inventário de riscos (PGR)"
        ordering = ["departamento__nome", "atividade"]

    def __str__(self):
        return f"{self.atividade}: {self.perigo}"

    @property
    def nivel(self):
        return (self.probabilidade or 0) * (self.severidade or 0)

    def classificacao(self):
        n = self.nivel
        return ("Trivial" if n <= 3 else "Tolerável" if n <= 6 else "Moderado" if n <= 12
                else "Substancial" if n <= 16 else "Intolerável")
    classificacao.short_description = "classificação"

    @property
    def status(self):  # etiqueta colorida: os níveis altos aparecem em vermelho
        c = self.classificacao()
        return {"Substancial": "Atenção: substancial", "Intolerável": "Vencido: intolerável"}.get(c, c)


class EntregaEPI(ModeloBase):
    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="epis")
    descricao = models.CharField("EPI", max_length=150)
    ca = models.CharField("CA (certificado de aprovação)", max_length=20)
    produto = models.ForeignKey("fiscal.Produto", null=True, blank=True, on_delete=models.PROTECT,
                                limit_choices_to={"tipo": "produto"},
                                help_text="Se informado, a entrega baixa o estoque.")
    quantidade = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    data_entrega = models.DateField("entregue em", default=timezone.localdate)
    validade = models.DateField("trocar até", null=True, blank=True)
    recebido_confirmado = models.BooleanField("colaborador confirmou o recebimento", default=False)

    class Meta:
        verbose_name = "entrega de EPI"
        verbose_name_plural = "entregas de EPI"
        ordering = ["-data_entrega"]

    def __str__(self):
        return f"{self.descricao} (CA {self.ca}) para {self.colaborador}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        novo = self.pk is None
        super().save(*args, **kwargs)
        if novo and self.produto_id:
            from apps.estoque import models as est

            est.saida(self.produto, self.quantidade, None, "epi", f"EPI {self.pk}", str(self.colaborador),
                      referencia=self)


class ExameOcupacional(ModeloBase):
    TIPOS = [("admissional", "Admissional"), ("periodico", "Periódico"), ("retorno", "Retorno ao trabalho"),
             ("mudanca", "Mudança de risco"), ("demissional", "Demissional")]
    RESULTADOS = [("apto", "Apto"), ("restricao", "Apto com restrição"), ("inapto", "Inapto")]
    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="asos")
    tipo = models.CharField(max_length=12, choices=TIPOS)
    data = models.DateField(default=timezone.localdate)
    resultado = models.CharField(max_length=10, choices=RESULTADOS,
                                 help_text="Só a aptidão. Não registre diagnóstico nem CID (LGPD).")
    validade = models.DateField("próximo exame até", null=True, blank=True)
    medico = models.CharField("médico / CRM", max_length=120, blank=True)

    class Meta:
        verbose_name = "ASO (exame ocupacional)"
        verbose_name_plural = "ASOs (exames ocupacionais)"
        ordering = ["-data"]

    def __str__(self):
        return f"ASO {self.get_tipo_display()} de {self.colaborador} ({self.data:%d/%m/%Y})"


class TreinamentoSST(ModeloBase):
    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="treinamentos_sst")
    norma = models.CharField(max_length=40, help_text="Ex.: NR-10, NR-35, NR-12, brigada de incêndio")
    descricao = models.CharField("curso", max_length=150)
    data = models.DateField("realizado em", default=timezone.localdate)
    carga_horaria = models.DecimalField("carga horária (h)", max_digits=6, decimal_places=1, default=0)
    validade = models.DateField("reciclagem até", null=True, blank=True)
    instrutor = models.CharField("instrutor / entidade", max_length=150, blank=True)

    class Meta:
        verbose_name = "treinamento de SST"
        verbose_name_plural = "treinamentos de SST"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.norma} — {self.colaborador}"


class MembroCIPA(ModeloBase):
    """Membro da CIPA com mandato (em regra de um ano). O alerta avisa o SESMT antes do fim do mandato."""

    FUNCOES = [("presidente", "Presidente"), ("vice", "Vice-presidente"), ("secretario", "Secretário"),
               ("titular", "Titular"), ("suplente", "Suplente")]
    REPRESENTACOES = [("empregador", "Representante do empregador"), ("empregados", "Representante dos empregados")]
    colaborador = models.ForeignKey(Colaborador, on_delete=models.PROTECT, related_name="mandatos_cipa")
    funcao = models.CharField("função", max_length=10, choices=FUNCOES, default="titular")
    representacao = models.CharField("representação", max_length=10, choices=REPRESENTACOES, default="empregados")
    inicio = models.DateField("início do mandato", default=timezone.localdate)
    fim = models.DateField("fim do mandato")

    class Meta:
        verbose_name = "membro da CIPA"
        verbose_name_plural = "membros da CIPA"
        ordering = ["-fim", "colaborador"]

    def __str__(self):
        return f"{self.colaborador} — {self.get_funcao_display()} ({self.inicio:%m/%Y} a {self.fim:%m/%Y})"

    def clean(self):
        if self.fim and self.inicio and self.fim <= self.inicio:
            raise ValidationError({"fim": "O fim do mandato deve ser depois do início."})

    @property
    def vigente(self):
        return self.inicio <= timezone.localdate() <= self.fim


class ReuniaoCIPA(ModeloBase):
    """Reunião da CIPA. Normalmente é mensal; sem reunião há mais de 35 dias o SESMT recebe um alerta."""

    TIPOS = [("ordinaria", "Ordinária (mensal)"), ("extraordinaria", "Extraordinária")]
    data = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=14, choices=TIPOS, default="ordinaria")
    pauta = models.TextField(blank=True)
    ata = models.TextField("ata / deliberações", blank=True)
    presentes = models.ManyToManyField(Colaborador, blank=True, related_name="reunioes_cipa")

    class Meta:
        verbose_name = "reunião da CIPA"
        verbose_name_plural = "reuniões da CIPA"
        ordering = ["-data"]

    def __str__(self):
        return f"Reunião da CIPA de {self.data:%d/%m/%Y}"


class LicencaAmbiental(ModeloBase):
    STATUS = [("vigente", "Vigente"), ("renovacao", "Em renovação"), ("encerrada", "Encerrada")]
    descricao = models.CharField("licença / autorização", max_length=150)
    orgao = models.CharField("órgão emissor", max_length=100, blank=True)
    numero = models.CharField("número", max_length=60, blank=True)
    emissao = models.DateField("emissão", default=timezone.localdate)
    validade = models.DateField("validade")
    condicionantes = models.TextField("condicionantes", blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="vigente")

    class Meta:
        verbose_name = "licença ambiental"
        verbose_name_plural = "licenças ambientais"
        ordering = ["validade"]

    def __str__(self):
        return f"{self.descricao} ({self.numero})" if self.numero else self.descricao

    def clean(self):
        if self.validade and self.emissao and self.validade < self.emissao:
            raise ValidationError({"validade": "A validade não pode ser anterior à emissão."})


class ResiduoGerado(ModeloBase):
    """Registro de resíduo gerado e destinado (inventário de resíduos): quantidade, classe, destino e MTR."""

    CLASSES = [("I", "Classe I — perigoso"), ("IIA", "Classe IIA — não perigoso, não inerte"),
               ("IIB", "Classe IIB — não perigoso, inerte")]
    UNIDADES = [("kg", "kg"), ("t", "t"), ("l", "L"), ("m3", "m³"), ("un", "unidade")]
    data = models.DateField(default=timezone.localdate)
    residuo = models.CharField("resíduo", max_length=150)
    classe = models.CharField(max_length=3, choices=CLASSES, default="IIA")
    quantidade = models.DecimalField(max_digits=12, decimal_places=3)
    unidade = models.CharField(max_length=3, choices=UNIDADES, default="kg")
    departamento = models.ForeignKey(Departamento, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    destinadora = models.CharField("transportador / destinação final", max_length=150, blank=True)
    mtr = models.CharField("MTR / manifesto", max_length=40, blank=True)
    certificado = models.CharField("certificado de destinação", max_length=60, blank=True)

    class Meta:
        verbose_name = "resíduo gerado"
        verbose_name_plural = "resíduos gerados"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.residuo}: {self.quantidade} {self.unidade}"

    def clean(self):
        if self.quantidade is not None and self.quantidade <= 0:
            raise ValidationError({"quantidade": "A quantidade deve ser positiva."})
        if self.classe == "I" and not self.mtr:
            raise ValidationError({"mtr": "Resíduo perigoso exige o número do MTR."})


def alertar_vencimentos(dias=None, hoje=None):
    """Rotina diária: ASOs e treinamentos que vencem na janela viram tarefa para o SESMT."""
    hoje = hoje or timezone.localdate()
    janela = hoje + datetime.timedelta(days=settings.SST_ALERTA_DIAS if dias is None else dias)
    criadas = 0
    ultimos_asos = {}
    for aso in ExameOcupacional.objects.filter(colaborador__status__in=("ativo", "ferias", "afastado")
                                               ).order_by("colaborador_id", "-data"):
        ultimos_asos.setdefault(aso.colaborador_id, aso)
    for aso in ultimos_asos.values():
        if aso.validade and aso.validade <= janela and aso.tipo != "demissional":
            if _tarefa_sesmt(aso, f"ASO periódico de {aso.colaborador} vence {aso.validade:%d/%m/%Y}",
                             "Agendar o exame periódico.", "alta", aso.validade):
                criadas += 1
    ultimos = {}
    for t in TreinamentoSST.objects.filter(colaborador__status__in=("ativo", "ferias")).order_by("-data"):
        ultimos.setdefault((t.colaborador_id, t.norma.upper()), t)
    for t in ultimos.values():
        if t.validade and t.validade <= janela:
            if _tarefa_sesmt(t, f"Reciclagem {t.norma} de {t.colaborador} vence {t.validade:%d/%m/%Y}",
                             f"Curso: {t.descricao}.", "alta", t.validade):
                criadas += 1
    for m in MembroCIPA.objects.filter(fim__gte=hoje, fim__lte=janela):
        if _tarefa_sesmt(m, f"Mandato da CIPA de {m.colaborador} termina {m.fim:%d/%m/%Y}",
                         "Preparar a eleição e a posse da nova gestão.", "alta", m.fim):
            criadas += 1
    if MembroCIPA.objects.filter(inicio__lte=hoje, fim__gte=hoje).exists():
        ultima = ReuniaoCIPA.objects.order_by("-data").first()
        if ultima is None or (hoje - ultima.data).days > 35:
            alvo = ultima or MembroCIPA.objects.filter(inicio__lte=hoje, fim__gte=hoje).first()
            if _tarefa_sesmt(alvo, "Reunião mensal da CIPA em atraso",
                             "Não há reunião registrada nos últimos 35 dias.", "alta", hoje):
                criadas += 1
    for lic in LicencaAmbiental.objects.filter(status__in=("vigente", "renovacao"), validade__lte=janela):
        if _tarefa_sesmt(lic, f"Licença ambiental vence {lic.validade:%d/%m/%Y}: {lic.descricao}",
                         "Iniciar a renovação junto ao órgão emissor.", "alta", lic.validade):
            criadas += 1
    return criadas


