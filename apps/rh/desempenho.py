"""Avaliação de desempenho (APD): competências, modelos com pesos, ciclos, autoavaliação + avaliação do gestor,
calibração e plano de desenvolvimento individual (PDI)."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.models import Colaborador, ModeloBase, PorEmpresaMixin

D2 = Decimal("0.01")


class Competencia(ModeloBase):
    nome = models.CharField(max_length=120, unique=True)
    descricao = models.TextField("descrição / comportamentos esperados", blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "competência"
        verbose_name_plural = "competências"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class ModeloAvaliacao(ModeloBase):
    nome = models.CharField(max_length=120, unique=True)
    escala_max = models.PositiveIntegerField("nota máxima da escala", default=5,
                                             help_text="As notas vão de 1 até este valor.")
    descricao = models.TextField("descrição", blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "modelo de avaliação"
        verbose_name_plural = "modelos de avaliação"
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def clean(self):
        if not 2 <= self.escala_max <= 10:
            raise ValidationError({"escala_max": "A escala deve ter de 2 a 10 pontos."})


class ItemModelo(models.Model):
    modelo = models.ForeignKey(ModeloAvaliacao, on_delete=models.CASCADE, related_name="itens")
    competencia = models.ForeignKey(Competencia, verbose_name="competência", on_delete=models.PROTECT, related_name="+")
    peso = models.DecimalField(max_digits=5, decimal_places=2, default=1)

    class Meta:
        verbose_name = "item do modelo"
        verbose_name_plural = "itens do modelo"
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["modelo", "competencia"], name="uq_item_modelo_competencia")]

    def __str__(self):
        return f"{self.competencia} (peso {self.peso})"

    def clean(self):
        if self.peso is not None and self.peso <= 0:
            raise ValidationError({"peso": "O peso deve ser maior que zero."})


class CicloAvaliacao(ModeloBase, PorEmpresaMixin):
    STATUS = [("rascunho", "Rascunho"), ("aberto", "Aberto"), ("calibracao", "Em calibração"),
              ("encerrado", "Encerrado")]

    nome = models.CharField(max_length=120)
    modelo = models.ForeignKey(ModeloAvaliacao, on_delete=models.PROTECT, related_name="ciclos")
    data_inicio = models.DateField("início", default=timezone.localdate)
    data_fim = models.DateField("prazo para responder", default=timezone.localdate)
    inclui_auto = models.BooleanField("inclui autoavaliação", default=True)
    peso_gestor = models.PositiveIntegerField("peso do gestor na nota final (%)", default=70,
                                              help_text="O restante é o peso da autoavaliação.")
    status = models.CharField(max_length=12, choices=STATUS, default="rascunho", editable=False)

    class Meta:
        verbose_name = "ciclo de avaliação"
        verbose_name_plural = "ciclos de avaliação"
        ordering = ["-data_inicio", "-id"]
        permissions = [("gerir_avaliacao", "Pode abrir ciclos, calibrar notas e ver todas as avaliações")]

    def __str__(self):
        return self.nome

    def clean(self):
        if self.data_fim and self.data_inicio and self.data_fim < self.data_inicio:
            raise ValidationError({"data_fim": "O prazo não pode ser antes do início."})
        if not 0 <= self.peso_gestor <= 100:
            raise ValidationError({"peso_gestor": "O peso vai de 0 a 100%."})

    def _exigir(self, *status):
        if self.status not in status:
            raise ValidationError(f"O ciclo está {self.get_status_display().lower()}; essa ação não se aplica agora.")

    @transaction.atomic
    def abrir(self):
        """Cria uma avaliação para cada colaborador ativo da empresa (o gestor vem do cadastro) e abre o ciclo."""
        self._exigir("rascunho")
        if not self.modelo.itens.exists():
            raise ValidationError("O modelo de avaliação não tem competências cadastradas.")
        qs = Colaborador.objects.exclude(status="desligado").filter(data_admissao__lte=self.data_inicio)
        if self.empresa_id:
            qs = qs.filter(empresa_id=self.empresa_id)
        n = 0
        for c in qs.select_related("gestor"):
            Avaliacao.objects.get_or_create(ciclo=self, colaborador=c, defaults={"gestor": c.gestor})
            n += 1
        if not n:
            raise ValidationError("Nenhum colaborador elegível (ativo e admitido até o início do ciclo).")
        self.status = "aberto"
        self.save(update_fields=["status", "atualizado_em"])
        return n

    def iniciar_calibracao(self):
        self._exigir("aberto")
        self.status = "calibracao"
        self.save(update_fields=["status", "atualizado_em"])

    @transaction.atomic
    def encerrar(self):
        self._exigir("aberto", "calibracao")
        for a in self.avaliacoes.all():
            a.fechar()
        self.status = "encerrado"
        self.save(update_fields=["status", "atualizado_em"])

    @property
    def peso_auto(self):
        return 100 - self.peso_gestor if self.inclui_auto else 0


def conceito(nota, escala):
    if nota is None:
        return ""
    pct = nota / escala * 100
    if pct < 40:
        return "Abaixo do esperado"
    if pct < 60:
        return "Atende parcialmente"
    if pct < 80:
        return "Atende"
    if pct < 90:
        return "Supera"
    return "Excepcional"


class Avaliacao(ModeloBase):
    STATUS = [("pendente", "Pendente"), ("andamento", "Em andamento"), ("concluida", "Concluída"),
              ("incompleta", "Incompleta (ciclo encerrado)")]

    ciclo = models.ForeignKey(CicloAvaliacao, on_delete=models.CASCADE, related_name="avaliacoes")
    colaborador = models.ForeignKey("core.Colaborador", on_delete=models.PROTECT, related_name="avaliacoes")
    gestor = models.ForeignKey("core.Colaborador", verbose_name="avaliador (gestor)", null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="avaliacoes_a_fazer")
    status = models.CharField(max_length=12, choices=STATUS, default="pendente", editable=False)
    auto_enviada_em = models.DateTimeField(null=True, blank=True, editable=False)
    gestor_enviada_em = models.DateTimeField(null=True, blank=True, editable=False)
    nota_auto = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
    nota_gestor = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
    nota_final = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
    nota_calibrada = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False)
    calibracao_motivo = models.CharField("motivo da calibração", max_length=300, blank=True, editable=False)
    calibrada_por = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+", editable=False)
    comentario_auto = models.TextField("comentário do colaborador", blank=True)
    comentario_gestor = models.TextField("comentário do gestor", blank=True)

    class Meta:
        verbose_name = "avaliação de desempenho"
        verbose_name_plural = "avaliações de desempenho"
        ordering = ["ciclo", "colaborador__nome"]
        constraints = [models.UniqueConstraint(fields=["ciclo", "colaborador"], name="uq_avaliacao_ciclo_colab")]

    def __str__(self):
        return f"{self.colaborador} — {self.ciclo}"

    # ---------------------------------------------------------------- acesso
    def pode_responder(self, usuario, tipo):
        colab = getattr(usuario, "colaborador", None) if usuario.is_authenticated else None
        if tipo == "auto":
            return colab is not None and colab.pk == self.colaborador_id
        if self.gestor_id:
            return colab is not None and colab.pk == self.gestor_id
        return usuario.has_perm("rh.gerir_avaliacao")   # sem gestor cadastrado, o RH avalia

    @property
    def nota_efetiva(self):
        return self.nota_calibrada if self.nota_calibrada is not None else self.nota_final

    @property
    def conceito(self):
        return conceito(self.nota_efetiva, self.ciclo.modelo.escala_max)

    # ---------------------------------------------------------------- respostas
    @transaction.atomic
    def responder(self, tipo, notas, comentario="", usuario=None, comentarios_item=None):
        """`notas`: {item_id: nota}. Cada parte só é enviada uma vez, com todas as competências."""
        if tipo not in ("auto", "gestor"):
            raise ValidationError("Tipo de avaliação inválido.")
        a = Avaliacao.objects.select_for_update().select_related("ciclo", "ciclo__modelo").get(pk=self.pk)
        if a.ciclo.status != "aberto":
            raise ValidationError("O ciclo não está aberto para respostas.")
        if tipo == "auto" and not a.ciclo.inclui_auto:
            raise ValidationError("Este ciclo não tem autoavaliação.")
        if usuario is not None and not a.pode_responder(usuario, tipo):
            raise ValidationError("Você não pode responder esta avaliação.")
        if getattr(a, f"{tipo}_enviada_em"):
            raise ValidationError("Esta avaliação já foi enviada e não pode ser alterada.")
        itens = list(a.ciclo.modelo.itens.select_related("competencia"))
        escala = a.ciclo.modelo.escala_max
        comentarios_item = comentarios_item or {}
        for it in itens:
            n = notas.get(it.pk)
            if n is None:
                raise ValidationError(f"Informe a nota de “{it.competencia}”.")
            if not 1 <= int(n) <= escala:
                raise ValidationError(f"A nota de “{it.competencia}” deve ficar entre 1 e {escala}.")
        total = sum(it.peso for it in itens)
        media = (sum(it.peso * int(notas[it.pk]) for it in itens) / total).quantize(D2)
        for it in itens:
            Resposta.objects.create(avaliacao=a, item=it, tipo=tipo, nota=int(notas[it.pk]),
                                    comentario=(comentarios_item.get(it.pk) or "")[:500])
        setattr(a, f"nota_{tipo}", media)
        setattr(a, f"comentario_{tipo}", comentario)
        setattr(a, f"{tipo}_enviada_em", timezone.now())
        a.status = "andamento"
        a._atualizar_final()
        a.save()
        for campo in ("nota_auto", "nota_gestor", "comentario_auto", "comentario_gestor", "auto_enviada_em",
                      "gestor_enviada_em", "nota_final", "status"):
            setattr(self, campo, getattr(a, campo))
        return media

    def _atualizar_final(self):
        c = self.ciclo
        if self.gestor_enviada_em and (not c.inclui_auto or self.auto_enviada_em):
            if c.inclui_auto:
                self.nota_final = ((self.nota_gestor * c.peso_gestor + self.nota_auto * c.peso_auto) / 100).quantize(D2)
            else:
                self.nota_final = self.nota_gestor
            self.status = "concluida"

    def fechar(self):
        """Ao encerrar o ciclo: avaliação sem as duas partes fica incompleta (sem nota final)."""
        if self.status != "concluida":
            self.status = "incompleta"
            self.save(update_fields=["status", "atualizado_em"])

    def calibrar(self, nota, motivo, usuario):
        status = CicloAvaliacao.objects.values_list("status", flat=True).get(pk=self.ciclo_id)
        if status != "calibracao":
            raise ValidationError("A calibração só é feita com o ciclo em calibração.")
        if self.status != "concluida":
            raise ValidationError("Só se calibra avaliação concluída (com as duas partes enviadas).")
        escala = self.ciclo.modelo.escala_max
        if nota is None or not 1 <= nota <= escala:
            raise ValidationError(f"A nota calibrada deve ficar entre 1 e {escala}.")
        if not motivo or not motivo.strip():
            raise ValidationError("Informe o motivo da calibração.")
        self.nota_calibrada = Decimal(nota).quantize(D2)
        self.calibracao_motivo = motivo.strip()[:300]
        self.calibrada_por = usuario
        self.save(update_fields=["nota_calibrada", "calibracao_motivo", "calibrada_por", "atualizado_em"])


class Resposta(models.Model):
    TIPOS = [("auto", "Autoavaliação"), ("gestor", "Gestor")]

    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, related_name="respostas")
    item = models.ForeignKey(ItemModelo, on_delete=models.PROTECT, related_name="+")
    tipo = models.CharField(max_length=6, choices=TIPOS)
    nota = models.PositiveIntegerField()
    comentario = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = "resposta"
        verbose_name_plural = "respostas"
        ordering = ["tipo", "item_id"]
        constraints = [models.UniqueConstraint(fields=["avaliacao", "item", "tipo"], name="uq_resposta_avaliacao")]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.item} = {self.nota}"


class AcaoDesenvolvimento(ModeloBase):
    """Item do plano de desenvolvimento individual (PDI) nascido de uma avaliação."""

    STATUS = [("aberta", "Aberta"), ("concluida", "Concluída"), ("cancelada", "Cancelada")]

    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, related_name="acoes")
    descricao = models.CharField("ação de desenvolvimento", max_length=300)
    competencia = models.ForeignKey(Competencia, verbose_name="competência", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    curso = models.ForeignKey("rh.Curso", verbose_name="curso sugerido", null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    prazo = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="aberta", editable=False)
    concluida_em = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "ação de desenvolvimento (PDI)"
        verbose_name_plural = "ações de desenvolvimento (PDI)"
        ordering = ["avaliacao", "prazo", "id"]

    def __str__(self):
        return self.descricao

    def concluir(self):
        if self.status != "aberta":
            raise ValidationError("A ação já está encerrada.")
        self.status, self.concluida_em = "concluida", timezone.localdate()
        self.save(update_fields=["status", "concluida_em", "atualizado_em"])

    def cancelar(self):
        if self.status != "aberta":
            raise ValidationError("A ação já está encerrada.")
        self.status = "cancelada"
        self.save(update_fields=["status", "atualizado_em"])
