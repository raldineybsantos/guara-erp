"""Recursos Humanos: tabelas legais, jornada e ponto, férias, benefícios, folha, rescisão e eSocial."""
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.validators import validar_cpf

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def rotulo_competencia(data):
    return f"{MESES[data.month]}/{data.year}"


# --------------------------------------------------------------------------- tabelas legais
class ParametroFolha(ModeloBase):
    """Tabelas legais com vigência (INSS, IRRF, salário-família, FGTS). Editáveis: a lei muda todo ano."""

    vigencia_inicio = models.DateField("vigente desde", unique=True)
    descricao = models.CharField("descrição", max_length=120, blank=True)
    salario_minimo = models.DecimalField("salário mínimo", max_digits=10, decimal_places=2)
    deducao_dependente = models.DecimalField("dedução por dependente (IRRF)", max_digits=10, decimal_places=2)
    desconto_simplificado = models.DecimalField("desconto simplificado mensal (IRRF)", max_digits=10,
                                                decimal_places=2, help_text="Usado quando for mais vantajoso que as "
                                                                            "deduções legais.")
    reducao_ate = models.DecimalField("redução do IRRF: isenção até", max_digits=10, decimal_places=2, default=0,
                                      help_text="Rendimentos até este valor: redução de até o 'valor máximo' abaixo "
                                                "(Lei 15.270/2025). Zero = sem redução.")
    reducao_maxima = models.DecimalField("redução máxima", max_digits=10, decimal_places=2, default=0)
    reducao_limite = models.DecimalField("redução decrescente até", max_digits=10, decimal_places=2, default=0)
    reducao_fixo = models.DecimalField("redução decrescente: parcela fixa", max_digits=10, decimal_places=2,
                                       default=0)
    reducao_fator = models.DecimalField("redução decrescente: fator", max_digits=10, decimal_places=6, default=0,
                                        help_text="Redução = parcela fixa − fator × rendimentos.")
    familia_limite = models.DecimalField("salário-família: limite de remuneração", max_digits=10, decimal_places=2)
    familia_cota = models.DecimalField("salário-família: cota por filho", max_digits=10, decimal_places=2)
    fgts_aliquota = models.DecimalField("FGTS (%)", max_digits=5, decimal_places=2, default=8)
    fgts_aprendiz = models.DecimalField("FGTS do aprendiz/estagiário (%)", max_digits=5, decimal_places=2, default=2)
    patronal_aliquota = models.DecimalField("INSS patronal (%)", max_digits=5, decimal_places=2, default=20,
                                            help_text="Não se aplica ao Simples Nacional/MEI.")
    rat = models.DecimalField("RAT/FAP (%)", max_digits=5, decimal_places=2, default=2)
    terceiros = models.DecimalField("terceiros (%)", max_digits=5, decimal_places=2, default=5.8)
    prolabore_inss = models.DecimalField("INSS do pró-labore (%)", max_digits=5, decimal_places=2, default=11)
    dia_pagamento = models.PositiveSmallIntegerField("dia do pagamento do salário (mês seguinte)", default=5)
    dia_guias = models.PositiveSmallIntegerField("dia de vencimento das guias (INSS, FGTS, IRRF)", default=20)

    class Meta:
        verbose_name = "tabela legal da folha"
        verbose_name_plural = "tabelas legais da folha"
        ordering = ["-vigencia_inicio"]

    def __str__(self):
        return f"Vigente desde {self.vigencia_inicio:%d/%m/%Y}" + (f" — {self.descricao}" if self.descricao else "")

    @classmethod
    def vigente(cls, data):
        p = cls.objects.filter(vigencia_inicio__lte=data).first()
        if p is None:
            raise ValidationError(f"Cadastre a tabela legal da folha vigente em {data:%d/%m/%Y} (RH → Tabelas legais).")
        return p

    def faixas_inss(self):
        return [(f.ate, f.aliquota) for f in self.faixas_inss_set.order_by("ate")]

    def faixas_irrf(self):
        return [(f.ate, f.aliquota, f.deduzir) for f in
                sorted(self.faixas_irrf_set.all(), key=lambda f: (f.ate is None, f.ate or 0))]


class FaixaINSS(models.Model):
    parametro = models.ForeignKey(ParametroFolha, on_delete=models.CASCADE, related_name="faixas_inss_set")
    ate = models.DecimalField("até (R$)", max_digits=10, decimal_places=2,
                              help_text="A última faixa é também o teto de contribuição.")
    aliquota = models.DecimalField("alíquota (%)", max_digits=5, decimal_places=2)

    class Meta:
        verbose_name = "faixa do INSS"
        verbose_name_plural = "faixas do INSS"
        ordering = ["ate"]


class FaixaIRRF(models.Model):
    parametro = models.ForeignKey(ParametroFolha, on_delete=models.CASCADE, related_name="faixas_irrf_set")
    ate = models.DecimalField("até (R$)", max_digits=10, decimal_places=2, null=True, blank=True,
                              help_text="Em branco na última faixa (sem limite).")
    aliquota = models.DecimalField("alíquota (%)", max_digits=5, decimal_places=2)
    deduzir = models.DecimalField("parcela a deduzir (R$)", max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "faixa do IRRF"
        verbose_name_plural = "faixas do IRRF"


# --------------------------------------------------------------------------- dados do vínculo
class Jornada(ModeloBase):
    nome = models.CharField(max_length=80, unique=True)
    horas_dia = models.DecimalField("horas por dia", max_digits=4, decimal_places=2, default=8)
    dias_semana = models.CharField("dias de trabalho", max_length=20, default="0,1,2,3,4",
                                   help_text="Números separados por vírgula: 0=segunda … 6=domingo.")
    intervalo_min = models.PositiveSmallIntegerField("intervalo (min)", default=60,
                                                     help_text="Descontado quando só há entrada e saída no dia.")
    tolerancia_min = models.PositiveSmallIntegerField("tolerância diária (min)", default=10)
    divisor_horas = models.DecimalField("divisor mensal de horas", max_digits=6, decimal_places=2, default=220)
    adicional_extra = models.DecimalField("adicional de hora extra (%)", max_digits=5, decimal_places=2, default=50)
    banco_horas = models.BooleanField("usa banco de horas", default=False,
                                      help_text="Com banco de horas, as extras não são pagas na folha: viram saldo.")
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "jornada"
        verbose_name_plural = "jornadas"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def clean(self):
        try:
            dias = {int(x) for x in self.dias_semana.replace(" ", "").split(",") if x != ""}
        except ValueError:
            raise ValidationError({"dias_semana": "Use números de 0 a 6 separados por vírgula."}) from None
        if not dias or any(d < 0 or d > 6 for d in dias):
            raise ValidationError({"dias_semana": "Use números de 0 (segunda) a 6 (domingo)."})

    @property
    def dias(self):
        return {int(x) for x in self.dias_semana.replace(" ", "").split(",") if x != ""}

    @property
    def esperado_min(self):
        return int(self.horas_dia * 60)


class DadosTrabalhistas(ModeloBase):
    """Complemento do colaborador exigido pela folha e pelo eSocial."""

    SEXOS = [("M", "Masculino"), ("F", "Feminino")]
    ESTADOS_CIVIS = [("1", "Solteiro"), ("2", "Casado"), ("3", "Divorciado"), ("4", "Separado"), ("5", "Viúvo")]
    INSTRUCAO = [("01", "Analfabeto"), ("02", "Fundamental incompleto"), ("04", "Fundamental completo"),
                 ("06", "Médio incompleto"), ("07", "Médio completo"), ("08", "Superior incompleto"),
                 ("09", "Superior completo"), ("10", "Pós-graduação"), ("11", "Mestrado"), ("12", "Doutorado")]
    RACAS = [("1", "Branca"), ("2", "Preta"), ("3", "Parda"), ("4", "Amarela"), ("5", "Indígena"),
             ("6", "Não informada")]

    colaborador = models.OneToOneField("core.Colaborador", on_delete=models.CASCADE, related_name="dados_trabalhistas")
    jornada = models.ForeignKey(Jornada, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    pis = models.CharField("PIS/NIS", max_length=14, blank=True)
    ctps_numero = models.CharField("CTPS (número)", max_length=15, blank=True)
    ctps_serie = models.CharField("CTPS (série)", max_length=10, blank=True)
    ctps_uf = models.CharField("CTPS (UF)", max_length=2, blank=True)
    sexo = models.CharField(max_length=1, choices=SEXOS, blank=True)
    estado_civil = models.CharField("estado civil", max_length=1, choices=ESTADOS_CIVIS, blank=True)
    grau_instrucao = models.CharField("grau de instrução", max_length=2, choices=INSTRUCAO, blank=True)
    raca_cor = models.CharField("raça/cor (eSocial)", max_length=1, choices=RACAS, default="6")
    categoria_esocial = models.CharField("categoria eSocial", max_length=3, default="101",
                                         help_text="101 empregado geral; 103 aprendiz; 721 estagiário; "
                                                   "722 diretor sem vínculo; 723 pró-labore.")
    cbo = models.CharField("CBO", max_length=7, blank=True)
    vale_transporte = models.DecimalField("vale-transporte: custo mensal", max_digits=10, decimal_places=2,
                                          default=0, help_text="Desconta 6% do salário, limitado ao custo.")
    pensao_percentual = models.DecimalField("pensão alimentícia (% do líquido legal)", max_digits=5,
                                            decimal_places=2, default=0)
    tipo_logradouro = models.CharField("tipo de logradouro (eSocial)", max_length=4, default="R",
                                       help_text="R = rua, AV = avenida, etc. (Tabela 20 do eSocial).")
    codigo_municipio = models.CharField("município de residência (IBGE)", max_length=7, blank=True)

    class Meta:
        verbose_name = "dados trabalhistas"
        verbose_name_plural = "dados trabalhistas"

    def __str__(self):
        return f"Dados trabalhistas de {self.colaborador}"


class Dependente(ModeloBase):
    PARENTESCOS = [("filho", "Filho(a)"), ("conjuge", "Cônjuge/companheiro(a)"), ("pai_mae", "Pai/mãe"),
                   ("enteado", "Enteado(a)"), ("tutelado", "Menor tutelado"), ("outro", "Outro")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.CASCADE, related_name="dependentes")
    nome = models.CharField(max_length=200)
    cpf = models.CharField("CPF", max_length=14, blank=True, validators=[validar_cpf])
    data_nascimento = models.DateField("data de nascimento")
    parentesco = models.CharField(max_length=10, choices=PARENTESCOS, default="filho")
    deduz_irrf = models.BooleanField("dedução no IRRF", default=True)
    invalido = models.BooleanField("inválido/incapaz", default=False)

    class Meta:
        verbose_name = "dependente"
        verbose_name_plural = "dependentes"
        ordering = ["colaborador__nome", "nome"]

    def __str__(self):
        return f"{self.nome} ({self.get_parentesco_display()})"

    def tem_salario_familia(self, hoje=None):
        """Filho/enteado/tutelado até 14 anos (completos no mês) ou inválido."""
        if self.parentesco not in ("filho", "enteado", "tutelado"):
            return False
        hoje = hoje or timezone.localdate()
        n = self.data_nascimento
        idade = hoje.year - n.year - ((hoje.month, hoje.day) < (n.month, n.day))
        return self.invalido or idade < 14


# --------------------------------------------------------------------------- ponto
class Feriado(ModeloBase):
    data = models.DateField(unique=True)
    nome = models.CharField(max_length=100)

    class Meta:
        verbose_name = "feriado"
        verbose_name_plural = "feriados"
        ordering = ["data"]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} — {self.nome}"


class Marcacao(ModeloBase):
    ORIGENS = [("web", "Pelo sistema"), ("manual", "Lançamento manual (RH)"), ("importado", "Importado")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="marcacoes")
    momento = models.DateTimeField(default=timezone.now, db_index=True)
    origem = models.CharField(max_length=10, choices=ORIGENS, default="web")
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    justificativa = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "marcação de ponto"
        verbose_name_plural = "marcações de ponto"
        ordering = ["-momento"]
        permissions = [("gerir_ponto", "Pode ver e ajustar o ponto de todos os colaboradores")]

    def __str__(self):
        return f"{self.colaborador} — {timezone.localtime(self.momento):%d/%m/%Y %H:%M}"

    def clean(self):
        if self.origem == "manual" and not self.justificativa:
            raise ValidationError({"justificativa": "Informe o motivo do lançamento manual."})


class AbonoPonto(ModeloBase):
    TIPOS = [("atestado", "Atestado médico"), ("justificada", "Falta justificada"), ("folga", "Folga/compensação"),
             ("ferias", "Férias/afastamento")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="abonos")
    data = models.DateField()
    tipo = models.CharField(max_length=12, choices=TIPOS, default="justificada")
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        verbose_name = "abono de ponto"
        verbose_name_plural = "abonos de ponto"
        ordering = ["-data"]
        unique_together = [("colaborador", "data")]

    def __str__(self):
        return f"{self.colaborador} — {self.data:%d/%m/%Y} ({self.get_tipo_display()})"


class ApuracaoDia(models.Model):
    """Resultado diário calculado a partir das marcações (recalculável)."""

    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.CASCADE, related_name="apuracoes")
    data = models.DateField(db_index=True)
    esperado_min = models.IntegerField(default=0)
    trabalhado_min = models.IntegerField(default=0)
    saldo_min = models.IntegerField(default=0)
    falta = models.BooleanField(default=False)
    inconsistente = models.BooleanField(default=False, help_text="Número ímpar de marcações no dia.")

    class Meta:
        verbose_name = "apuração diária"
        verbose_name_plural = "apurações diárias"
        unique_together = [("colaborador", "data")]
        ordering = ["-data"]


class AjusteBancoHoras(ModeloBase):
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="ajustes_banco")
    data = models.DateField(default=timezone.localdate)
    minutos = models.IntegerField(help_text="Positivo credita, negativo debita (compensação ou pagamento).")
    motivo = models.CharField(max_length=200)

    class Meta:
        verbose_name = "ajuste do banco de horas"
        verbose_name_plural = "ajustes do banco de horas"
        ordering = ["-data"]

    def __str__(self):
        return f"{self.colaborador} {self.minutos:+d} min"


# --------------------------------------------------------------------------- férias
class PeriodoAquisitivo(ModeloBase):
    STATUS = [("aberto", "Em aquisição"), ("disponivel", "Disponível para gozo"), ("gozado", "Totalmente gozado"),
              ("vencido", "Vencido (concessivo expirou)")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.CASCADE, related_name="periodos_ferias")
    inicio = models.DateField("início")
    fim = models.DateField()
    dias_direito = models.PositiveSmallIntegerField("dias de direito", default=30,
                                                    help_text="Reduzido pelas faltas injustificadas (CLT art. 130).")

    class Meta:
        verbose_name = "período aquisitivo"
        verbose_name_plural = "períodos aquisitivos"
        ordering = ["colaborador__nome", "-inicio"]
        unique_together = [("colaborador", "inicio")]

    def __str__(self):
        return f"{self.colaborador} — {self.inicio:%d/%m/%Y} a {self.fim:%d/%m/%Y}"

    @property
    def limite_concessivo(self):
        try:
            return self.fim.replace(year=self.fim.year + 1)
        except ValueError:
            return self.fim.replace(year=self.fim.year + 1, day=28)

    @property
    def dias_usados(self):
        s = self.solicitacoes.filter(status__in=["aprovada", "gozada"])
        return sum(x.dias + x.abono_dias for x in s)

    @property
    def saldo(self):
        return max(self.dias_direito - self.dias_usados, 0)

    @property
    def situacao(self):
        hoje = timezone.localdate()
        if hoje <= self.fim:
            return "aberto"
        if self.saldo == 0:
            return "gozado"
        return "vencido" if hoje > self.limite_concessivo else "disponivel"

    @property
    def situacao_rotulo(self):
        return dict(self.STATUS)[self.situacao]


class SolicitacaoFerias(ModeloBase):
    STATUS = [("solicitada", "Solicitada"), ("aprovada", "Aprovada"), ("recusada", "Recusada"),
              ("gozada", "Gozada"), ("cancelada", "Cancelada")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="solicitacoes_ferias")
    periodo = models.ForeignKey(PeriodoAquisitivo, verbose_name="período aquisitivo", on_delete=models.PROTECT,
                                related_name="solicitacoes")
    inicio = models.DateField("início do gozo")
    dias = models.PositiveSmallIntegerField("dias de gozo")
    abono_dias = models.PositiveSmallIntegerField("abono pecuniário (dias vendidos, até 10)", default=0)
    adiantar_13 = models.BooleanField("adiantar 1ª parcela do 13º", default=False)
    status = models.CharField(max_length=12, choices=STATUS, default="solicitada", db_index=True, editable=False)
    decidido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", editable=False)
    decidido_em = models.DateTimeField(null=True, blank=True, editable=False)
    motivo_recusa = models.CharField(max_length=200, blank=True, editable=False)

    class Meta:
        verbose_name = "solicitação de férias"
        verbose_name_plural = "solicitações de férias"
        ordering = ["-inicio"]
        permissions = [("aprovar_solicitacaoferias", "Pode aprovar ou recusar férias"),
                       ("gerir_ferias", "Pode ver as férias de todos os colaboradores")]

    def __str__(self):
        return f"{self.colaborador} — {self.inicio:%d/%m/%Y} ({self.dias} dias)"

    @property
    def fim(self):
        return self.inicio + datetime.timedelta(days=max(self.dias - 1, 0))

    def clean(self):
        erros = {}
        if self.dias and not 5 <= self.dias <= 30:
            erros["dias"] = "Cada período de férias tem de 5 a 30 dias."
        if self.abono_dias > 10:
            erros["abono_dias"] = "O abono pecuniário é de até 10 dias (1/3 das férias)."
        if erros:
            raise ValidationError(erros)
        if self.periodo_id and self.colaborador_id and self.periodo.colaborador_id != self.colaborador_id:
            raise ValidationError({"periodo": "O período aquisitivo é de outro colaborador."})


# --------------------------------------------------------------------------- benefícios
class Beneficio(ModeloBase):
    TIPOS = [("vr", "Vale-refeição"), ("va", "Vale-alimentação"), ("vt", "Vale-transporte"),
             ("saude", "Plano de saúde"), ("odonto", "Plano odontológico"), ("outro", "Outro")]
    nome = models.CharField(max_length=80, unique=True)
    tipo = models.CharField(max_length=10, choices=TIPOS, default="outro")
    valor_mensal = models.DecimalField("custo mensal para a empresa", max_digits=10, decimal_places=2, default=0)
    desconto_percentual = models.DecimalField("desconto do colaborador (% do salário)", max_digits=5,
                                              decimal_places=2, default=0)
    desconto_fixo = models.DecimalField("desconto do colaborador (R$ fixo)", max_digits=10, decimal_places=2,
                                        default=0)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "benefício"
        verbose_name_plural = "benefícios"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class BeneficioColaborador(ModeloBase):
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.CASCADE, related_name="beneficios")
    beneficio = models.ForeignKey(Beneficio, verbose_name="benefício", on_delete=models.PROTECT, related_name="+")
    valor = models.DecimalField("custo mensal (se diferente do padrão)", max_digits=10, decimal_places=2, null=True,
                                blank=True)
    inicio = models.DateField("início", default=timezone.localdate)
    fim = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "benefício do colaborador"
        verbose_name_plural = "benefícios dos colaboradores"
        ordering = ["colaborador__nome", "beneficio__nome"]

    def __str__(self):
        return f"{self.colaborador} — {self.beneficio}"

    def vigente_em(self, data):
        return self.inicio <= data and (self.fim is None or self.fim >= data)


# --------------------------------------------------------------------------- folha
class Rubrica(ModeloBase):
    TIPOS = [("provento", "Provento"), ("desconto", "Desconto"), ("informativo", "Informativo")]
    codigo = models.CharField("código", max_length=10, unique=True)
    nome = models.CharField(max_length=80)
    tipo = models.CharField(max_length=12, choices=TIPOS)
    incide_inss = models.BooleanField("incide INSS", default=False)
    incide_irrf = models.BooleanField("incide IRRF", default=False)
    incide_fgts = models.BooleanField("incide FGTS", default=False)
    natureza_esocial = models.CharField("natureza eSocial (tabela 3)", max_length=6, blank=True,
                                        help_text="Código da natureza da rubrica, ex.: 1000 salário.")
    sistema = models.BooleanField(default=False, editable=False, help_text="Usada pelos cálculos automáticos.")

    class Meta:
        verbose_name = "rubrica"
        verbose_name_plural = "rubricas"
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.codigo} {self.nome}"


class LancamentoFolha(ModeloBase):
    ORIGENS = [("manual", "Manual"), ("ponto", "Ponto")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="lancamentos_folha")
    competencia = models.DateField("competência", help_text="Use o dia 1 do mês.")
    rubrica = models.ForeignKey(Rubrica, on_delete=models.PROTECT, related_name="+")
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    referencia = models.CharField("referência", max_length=30, blank=True, help_text="Ex.: 12h, 2 dias.")
    origem = models.CharField(max_length=8, choices=ORIGENS, default="manual", editable=False)

    class Meta:
        verbose_name = "lançamento da folha"
        verbose_name_plural = "lançamentos da folha"
        ordering = ["-competencia", "colaborador__nome"]

    def __str__(self):
        return f"{self.colaborador} — {self.rubrica.nome} {self.valor}"

    def clean(self):
        if self.competencia:
            self.competencia = self.competencia.replace(day=1)


class Folha(ModeloBase, PorEmpresaMixin):
    TIPOS = [("mensal", "Folha mensal"), ("13_1", "13º salário — 1ª parcela"), ("13_2", "13º salário — 2ª parcela")]
    STATUS = [("aberta", "Aberta"), ("calculada", "Calculada"), ("fechada", "Fechada")]
    competencia = models.DateField("competência", help_text="Use o dia 1 do mês.")
    tipo = models.CharField(max_length=6, choices=TIPOS, default="mensal")
    status = models.CharField(max_length=10, choices=STATUS, default="aberta", editable=False, db_index=True)
    total_bruto = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_descontos = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_liquido = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_inss = models.DecimalField("INSS dos colaboradores", max_digits=14, decimal_places=2, default=0,
                                     editable=False)
    total_irrf = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_fgts = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_patronal = models.DecimalField("encargos patronais (INSS/RAT/terceiros)", max_digits=14,
                                         decimal_places=2, default=0, editable=False)
    fechada_em = models.DateTimeField(null=True, blank=True, editable=False)
    fechada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    titulos_ids = models.JSONField(default=list, blank=True, editable=False)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "folha de pagamento"
        verbose_name_plural = "folhas de pagamento"
        ordering = ["-competencia", "tipo"]
        unique_together = [("empresa", "competencia", "tipo")]
        permissions = [("fechar_folha", "Pode calcular, fechar e reabrir a folha")]

    def __str__(self):
        return f"{self.get_tipo_display()} — {rotulo_competencia(self.competencia)}"

    def clean(self):
        if self.competencia:
            self.competencia = self.competencia.replace(day=1)


class Holerite(models.Model):
    folha = models.ForeignKey(Folha, on_delete=models.CASCADE, related_name="holerites")
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="holerites")
    salario_base = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bruto = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    base_inss = models.DecimalField("base do INSS", max_digits=12, decimal_places=2, default=0)
    inss = models.DecimalField("INSS", max_digits=12, decimal_places=2, default=0)
    base_irrf = models.DecimalField("base do IRRF", max_digits=12, decimal_places=2, default=0)
    irrf = models.DecimalField("IRRF", max_digits=12, decimal_places=2, default=0)
    fgts = models.DecimalField("FGTS (depósito)", max_digits=12, decimal_places=2, default=0)
    patronal = models.DecimalField("encargos patronais", max_digits=12, decimal_places=2, default=0)
    descontos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    liquido = models.DecimalField("líquido", max_digits=12, decimal_places=2, default=0)
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        verbose_name = "holerite"
        verbose_name_plural = "holerites"
        ordering = ["colaborador__nome"]
        unique_together = [("folha", "colaborador")]

    def __str__(self):
        return f"{self.colaborador} — {self.folha}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("rh:holerite_ver", args=[self.pk])


class ItemHolerite(models.Model):
    holerite = models.ForeignKey(Holerite, on_delete=models.CASCADE, related_name="itens")
    codigo = models.CharField(max_length=10)
    descricao = models.CharField(max_length=120)
    tipo = models.CharField(max_length=12, choices=Rubrica.TIPOS)
    referencia = models.CharField(max_length=30, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]


# --------------------------------------------------------------------------- rescisão
class Rescisao(ModeloBase, PorEmpresaMixin):
    MOTIVOS = [("sem_justa_causa", "Dispensa sem justa causa"), ("pedido", "Pedido de demissão"),
               ("justa_causa", "Dispensa por justa causa"), ("acordo", "Acordo (CLT art. 484-A)"),
               ("fim_contrato", "Término de contrato determinado")]
    AVISOS = [("indenizado", "Aviso prévio indenizado"), ("trabalhado", "Aviso prévio trabalhado"),
              ("dispensado", "Sem aviso / dispensado"), ("nao_cumprido", "Não cumprido pelo colaborador")]
    STATUS = [("calculada", "Calculada"), ("efetivada", "Efetivada")]
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="rescisoes")
    data_desligamento = models.DateField("data do desligamento")
    motivo = models.CharField(max_length=16, choices=MOTIVOS)
    aviso = models.CharField("aviso prévio", max_length=12, choices=AVISOS, default="indenizado")
    saldo_fgts = models.DecimalField("saldo do FGTS (extrato)", max_digits=14, decimal_places=2, default=0,
                                     help_text="Saldo da conta vinculada, do extrato da Caixa. Base da multa.")
    status = models.CharField(max_length=10, choices=STATUS, default="calculada", editable=False)
    memoria = models.JSONField("memória de cálculo", default=list, blank=True, editable=False)
    total_proventos = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_descontos = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_liquido = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    multa_fgts = models.DecimalField("multa do FGTS", max_digits=14, decimal_places=2, default=0, editable=False)
    titulo_id = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "rescisão"
        verbose_name_plural = "rescisões"
        ordering = ["-data_desligamento"]
        permissions = [("efetivar_rescisao", "Pode efetivar rescisões")]

    def __str__(self):
        return f"{self.colaborador} — {self.get_motivo_display()}"


# --------------------------------------------------------------------------- eSocial
class ConfiguracaoESocial(ModeloBase):
    """Dados do empregador exigidos pelos eventos S-1000 e S-1299."""

    empresa = models.OneToOneField("core.Empresa", on_delete=models.CASCADE, related_name="config_esocial")
    contato_nome = models.CharField("contato: nome", max_length=70)
    contato_cpf = models.CharField("contato: CPF", max_length=14, validators=[validar_cpf])
    contato_telefone = models.CharField("contato: telefone", max_length=13, blank=True)
    contato_email = models.EmailField("contato: e-mail", blank=True)
    porte_me_epp = models.BooleanField("ME/EPP (porte)", default=True)

    class Meta:
        verbose_name = "configuração do eSocial"
        verbose_name_plural = "configuração do eSocial"

    def __str__(self):
        return f"eSocial de {self.empresa}"


class EventoESocial(ModeloBase, PorEmpresaMixin):
    TIPOS = [("S-1000", "S-1000 Informações do empregador"), ("S-1010", "S-1010 Tabela de rubricas"),
             ("S-2200", "S-2200 Admissão"), ("S-1200", "S-1200 Remuneração"), ("S-1210", "S-1210 Pagamentos"),
             ("S-1299", "S-1299 Fechamento dos eventos periódicos"), ("S-2299", "S-2299 Desligamento")]
    STATUS = [("gerado", "Gerado"), ("assinado", "Assinado"), ("enviado", "Enviado"), ("aceito", "Aceito"),
              ("erro", "Com erro")]
    tipo = models.CharField(max_length=6, choices=TIPOS, db_index=True)
    colaborador = models.ForeignKey("core.Colaborador", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    competencia = models.DateField("competência", null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="gerado", db_index=True)
    identificador = models.CharField("Id do evento", max_length=40, blank=True, editable=False)
    xml = models.TextField(blank=True, editable=False)
    xml_assinado = models.TextField(blank=True, editable=False)
    recibo = models.CharField("nº do recibo", max_length=60, blank=True)
    retorno = models.TextField("retorno do governo", blank=True)

    class Meta:
        verbose_name = "evento do eSocial"
        verbose_name_plural = "eventos do eSocial"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"{self.tipo} {self.colaborador or self.empresa}"
