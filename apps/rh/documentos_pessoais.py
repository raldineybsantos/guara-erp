"""Documentos pessoais do colaborador e de seus dependentes (RG, comprovante de residência, certidões, vacinação…).

O arquivo fica no GED (pasta restrita ao RH, confidencial, com antivírus e versões); aqui ficam o vínculo, o
catálogo de documentos exigidos e o cálculo das pendências."""
import datetime

from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone

from apps.core.models import ModeloBase


def idade_em(nascimento, hoje=None):
    hoje = hoje or timezone.localdate()
    if not nascimento:
        return None
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


class TipoDocumentoPessoal(ModeloBase):
    """Catálogo dos documentos que o RH exige. Editável: a empresa define o que é obrigatório."""
    ALVOS = [("colaborador", "Colaborador"), ("conjuge", "Cônjuge/companheiro(a)"), ("filho", "Filho(a)/enteado(a)/tutelado(a)"),
             ("dependente", "Outro dependente")]
    nome = models.CharField(max_length=120)
    alvo = models.CharField("de quem", max_length=12, choices=ALVOS, default="colaborador")
    obrigatorio = models.BooleanField("obrigatório", default=True)
    somente_clt = models.BooleanField("somente para CLT", default=False)
    idade_minima = models.PositiveSmallIntegerField("a partir de (anos)", null=True, blank=True,
                                                    help_text="Dependentes: exigir só a partir desta idade.")
    idade_maxima = models.PositiveSmallIntegerField("até (anos)", null=True, blank=True,
                                                    help_text="Dependentes: exigir só até esta idade (ex.: vacinação até 7).")
    exige_validade = models.BooleanField("tem validade", default=False,
                                         help_text="Pede a data de validade e cobra a renovação (ex.: ASO).")
    ordem = models.PositiveSmallIntegerField(default=10)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tipo de documento pessoal"
        verbose_name_plural = "tipos de documento pessoal"
        ordering = ["alvo", "ordem", "nome"]
        unique_together = [("nome", "alvo")]

    def __str__(self):
        return f"{self.nome} ({self.get_alvo_display()})"

    def vale_para(self, colaborador, dependente=None, hoje=None):
        if not self.ativo or not self.obrigatorio:
            return False
        if self.somente_clt and colaborador.tipo_contrato != "clt":
            return False
        if dependente is not None:
            idade = idade_em(dependente.data_nascimento, hoje)
            if self.idade_minima is not None and idade < self.idade_minima:
                return False
            if self.idade_maxima is not None and idade > self.idade_maxima:
                return False
        return True


class DocumentoPessoal(ModeloBase):
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.CASCADE, related_name="documentos_pessoais")
    dependente = models.ForeignKey("rh.Dependente", on_delete=models.CASCADE, null=True, blank=True,
                                   related_name="documentos_pessoais",
                                   help_text="Deixe em branco para documentos do próprio colaborador.")
    tipo = models.ForeignKey(TipoDocumentoPessoal, on_delete=models.PROTECT, related_name="documentos")
    numero = models.CharField("número", max_length=60, blank=True)
    data_emissao = models.DateField("data de emissão", null=True, blank=True)
    validade = models.DateField(null=True, blank=True)
    documento = models.ForeignKey("ged.Documento", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                  editable=False)
    observacoes = models.CharField("observações", max_length=300, blank=True)

    class Meta:
        verbose_name = "documento pessoal"
        verbose_name_plural = "documentos pessoais"
        ordering = ["colaborador__nome", "dependente__nome", "tipo__ordem"]

    def __str__(self):
        de = self.dependente.nome if self.dependente_id else self.colaborador.nome
        return f"{self.tipo.nome} — {de}"

    @property
    def vencido(self):
        return bool(self.validade and self.validade < timezone.localdate())

    @property
    def versao(self):
        return self.documento.versao_atual if self.documento_id else None

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("rh:documentopessoal_detail", args=[self.pk])


def pasta_documentos_pessoais():
    from apps.ged.models import Pasta

    pasta, criada = Pasta.objects.get_or_create(nome="RH — Documentos pessoais", superior=None)
    if criada:
        pasta.grupos_acesso.add(*Group.objects.filter(name__in=["RH", "Administradores"]))
    return pasta


def pendencias(colaborador, hoje=None):
    """Lista de dicts {alvo, tipo, motivo} com o que falta (ou venceu) para o colaborador e seus dependentes."""
    hoje = hoje or timezone.localdate()
    enviados = {}
    for d in colaborador.documentos_pessoais.select_related("tipo"):
        enviados.setdefault((d.dependente_id, d.tipo_id), []).append(d)
    tipos = list(TipoDocumentoPessoal.objects.filter(ativo=True, obrigatorio=True))
    saida = []

    def checar(rotulo, dep, alvos):
        for t in tipos:
            if t.alvo not in alvos or not t.vale_para(colaborador, dep, hoje):
                continue
            docs = enviados.get((dep.pk if dep else None, t.pk), [])
            if not docs:
                saida.append({"alvo": rotulo, "tipo": t, "motivo": "não enviado"})
            elif t.exige_validade and not any(d.validade and d.validade >= hoje for d in docs):
                saida.append({"alvo": rotulo, "tipo": t, "motivo": "vencido" if any(d.validade for d in docs) else "sem validade informada"})

    checar(colaborador.nome, None, {"colaborador"})
    for dep in colaborador.dependentes.all():
        if dep.parentesco == "conjuge":
            checar(f"{dep.nome} (cônjuge)", dep, {"conjuge"})
        elif dep.parentesco in ("filho", "enteado", "tutelado"):
            checar(f"{dep.nome} (filho/a)", dep, {"filho"})
        else:
            checar(f"{dep.nome} ({dep.get_parentesco_display()})", dep, {"dependente"})
    return saida


PADRAO = [
    # (nome, alvo, somente_clt, idade_min, idade_max, validade, ordem)
    ("RG ou CNH", "colaborador", False, None, None, False, 1),
    ("CPF", "colaborador", False, None, None, False, 2),
    ("Comprovante de residência", "colaborador", False, None, None, False, 3),
    ("Título de eleitor", "colaborador", False, None, None, False, 4),
    ("Certidão de nascimento ou casamento", "colaborador", False, None, None, False, 5),
    ("Carteira de Trabalho (CTPS)", "colaborador", True, None, None, False, 6),
    ("Foto 3x4", "colaborador", False, None, None, False, 7),
    ("ASO admissional", "colaborador", True, None, None, True, 8),
    ("Carteira de vacinação", "colaborador", False, None, None, False, 9),
    ("RG ou CPF do cônjuge", "conjuge", False, None, None, False, 1),
    ("Certidão de casamento ou união estável", "conjuge", False, None, None, False, 2),
    ("Certidão de nascimento do filho", "filho", False, None, None, False, 1),
    ("CPF do filho", "filho", False, None, None, False, 2),
    ("Carteira de vacinação do filho", "filho", False, None, 7, False, 3),
    ("Comprovante de matrícula escolar", "filho", False, 4, 17, False, 4),
]


def criar_tipos_padrao():
    n = 0
    for nome, alvo, clt, imin, imax, val, ordem in PADRAO:
        _, nova = TipoDocumentoPessoal.objects.get_or_create(
            nome=nome, alvo=alvo, defaults=dict(somente_clt=clt, idade_minima=imin, idade_maxima=imax,
                                                exige_validade=val, ordem=ordem))
        n += nova
    return n
