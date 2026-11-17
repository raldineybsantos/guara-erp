"""Qualidade: inspeção de recebimento (quarentena) e não conformidades com causa e ação corretiva."""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase, PorEmpresaMixin
from apps.core.numeracao import NumeradoMixin
from apps.core.formatos import qtd as fq


class PlanoInspecao(ModeloBase):
    """Características a medir ou verificar na inspeção de um produto, com a faixa aceitável."""

    produto = models.OneToOneField("fiscal.Produto", on_delete=models.PROTECT, related_name="plano_inspecao")
    observacoes = models.TextField("método / instrumentos", blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "plano de inspeção"
        verbose_name_plural = "planos de inspeção"
        ordering = ["produto__descricao"]

    def __str__(self):
        return f"Plano de inspeção de {self.produto}"


class CaracteristicaInspecao(models.Model):
    TIPOS = [("quantitativa", "Medida (número dentro de uma faixa)"), ("qualitativa", "Verificação (atende / não atende)")]
    plano = models.ForeignKey(PlanoInspecao, on_delete=models.CASCADE, related_name="caracteristicas")
    descricao = models.CharField("característica", max_length=150)
    tipo = models.CharField(max_length=12, choices=TIPOS, default="quantitativa")
    minimo = models.DecimalField("mínimo", max_digits=14, decimal_places=4, null=True, blank=True)
    maximo = models.DecimalField("máximo", max_digits=14, decimal_places=4, null=True, blank=True)
    unidade = models.CharField(max_length=10, blank=True)

    class Meta:
        verbose_name = "característica"
        verbose_name_plural = "características"
        ordering = ["pk"]

    def __str__(self):
        return self.descricao

    def clean(self):
        if self.minimo is not None and self.maximo is not None and self.minimo > self.maximo:
            raise ValidationError({"maximo": "O máximo não pode ser menor que o mínimo."})
        if self.tipo == "quantitativa" and self.minimo is None and self.maximo is None:
            raise ValidationError("Informe o mínimo e/ou o máximo da medida.")


class Inspecao(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "INS"
    ORIGENS = [("recebimento", "Recebimento de compra"), ("producao", "Produção"), ("manual", "Manual")]
    STATUS = [("pendente", "Pendente"), ("aprovada", "Aprovada"), ("parcial", "Aprovada parcialmente"),
              ("reprovada", "Reprovada")]
    produto = models.ForeignKey("fiscal.Produto", on_delete=models.PROTECT, related_name="+")
    quantidade = models.DecimalField(max_digits=14, decimal_places=4)
    armazem = models.ForeignKey("estoque.Armazem", verbose_name="armazém (quarentena)", on_delete=models.PROTECT)
    origem = models.CharField(max_length=12, choices=ORIGENS, default="manual")
    recebimento = models.ForeignKey("compras.RecebimentoCompra", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="inspecoes")
    fornecedor = models.ForeignKey("core.Fornecedor", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    status = models.CharField(max_length=10, choices=STATUS, default="pendente", editable=False, db_index=True)
    quantidade_aprovada = models.DecimalField("aprovado", max_digits=14, decimal_places=4, default=0, editable=False)
    inspetor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+", editable=False)
    data_inspecao = models.DateTimeField("inspecionado em", null=True, blank=True, editable=False)
    resultado = models.TextField("resultado / laudo", blank=True)
    nao_conformidade = models.ForeignKey("NaoConformidade", null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="+", editable=False)

    class Meta:
        verbose_name = "inspeção"
        verbose_name_plural = "inspeções"
        ordering = ["-numero"]

    def save(self, *args, **kwargs):
        if not self.empresa_id and self.armazem_id:
            self.empresa_id = self.armazem.empresa_id      # a inspeção é da empresa do armazém de quarentena
        novo = self.pk is None
        super().save(*args, **kwargs)
        if novo:
            self.gerar_resultados()

    def gerar_resultados(self):
        """Cria uma linha de resultado para cada característica do plano ativo do produto (uma vez só)."""
        if self.resultados.exists():
            return 0
        plano = PlanoInspecao.objects.filter(produto_id=self.produto_id, ativo=True).first()
        if not plano:
            return 0
        linhas = [ResultadoInspecao(inspecao=self, caracteristica=c) for c in plano.caracteristicas.all()]
        ResultadoInspecao.objects.bulk_create(linhas)
        return len(linhas)

    def pendencias(self):
        """Resultados ainda sem preenchimento; linhas apagadas do plano contam como pendentes."""
        self.gerar_resultados()
        pendentes = [r for r in self.resultados.select_related("caracteristica") if r.conforme is None]
        plano = PlanoInspecao.objects.filter(produto_id=self.produto_id, ativo=True).first()
        if plano:
            feitas = set(self.resultados.values_list("caracteristica_id", flat=True))
            faltam = [c for c in plano.caracteristicas.all() if c.pk not in feitas]
            if faltam and self.resultados.exists():
                pendentes += [ResultadoInspecao(inspecao=self, caracteristica=c) for c in faltam]
        return pendentes

    def fora_de_especificacao(self):
        return [r for r in self.resultados.select_related("caracteristica") if r.conforme is False]

    @transaction.atomic
    def concluir(self, usuario, quantidade_aprovada, resultado):
        """Libera o aprovado para o armazém principal; o reprovado fica na quarentena e abre não conformidade."""
        from apps.estoque import models as est

        if self.status != "pendente":
            raise ValidationError("Inspeção já concluída.")
        aprovada = min(max(quantidade_aprovada, 0), self.quantidade)
        if self.pendencias():
            raise ValidationError("Preencha todas as medições e verificações do plano de inspeção antes de concluir.")
        fora = self.fora_de_especificacao()
        if fora and aprovada == self.quantidade:
            raise ValidationError("Há característica fora da especificação (" + ", ".join(r.caracteristica.descricao for r in fora)
                                  + "): reprove tudo ou libere só a parte conforme.")
        if aprovada > 0:
            est.transferir(self.produto, aprovada, self.armazem, est.Armazem.principal(self.empresa), self.numero,
                           "Liberado pela Qualidade", referencia=self)
        reprovada = self.quantidade - aprovada
        self.quantidade_aprovada = aprovada
        self.status = "aprovada" if reprovada == 0 else ("reprovada" if aprovada == 0 else "parcial")
        self.inspetor, self.data_inspecao, self.resultado = usuario, timezone.now(), resultado
        if reprovada > 0:
            self.nao_conformidade = NaoConformidade.objects.create(
                titulo=f"Reprovação na {self.numero}: {self.produto}"[:200], origem="recebimento"
                if self.origem == "recebimento" else "producao", produto=self.produto, fornecedor=self.fornecedor,
                descricao=f"{fq(reprovada)} {self.produto.unidade} reprovados de {fq(self.quantidade)}.\n\n{resultado}",
                gravidade="media", responsavel=usuario)
        self.save()
        return self.status


class ResultadoInspecao(models.Model):
    inspecao = models.ForeignKey(Inspecao, on_delete=models.CASCADE, related_name="resultados")
    caracteristica = models.ForeignKey(CaracteristicaInspecao, on_delete=models.PROTECT, related_name="+")
    valor = models.DecimalField("valor medido", max_digits=14, decimal_places=4, null=True, blank=True)
    atendeu = models.BooleanField("atende?", null=True, blank=True)
    observacao = models.CharField("observação", max_length=200, blank=True)

    class Meta:
        verbose_name = "resultado da característica"
        verbose_name_plural = "resultados do plano de inspeção"
        ordering = ["pk"]

    def __str__(self):
        return f"{self.caracteristica}: {self.valor if self.valor is not None else self.atendeu}"

    @property
    def conforme(self):
        """True/False; None enquanto não foi preenchido."""
        c = self.caracteristica
        if c.tipo == "qualitativa":
            return self.atendeu
        if self.valor is None:
            return None
        return not ((c.minimo is not None and self.valor < c.minimo) or (c.maximo is not None and self.valor > c.maximo))


class NaoConformidade(NumeradoMixin, ModeloBase, PorEmpresaMixin):
    PREFIXO = "NC"
    ORIGENS = [("recebimento", "Recebimento / fornecedor"), ("producao", "Produção"), ("cliente", "Reclamação de cliente"),
               ("interna", "Processo interno"), ("auditoria", "Auditoria")]
    GRAVIDADES = [("baixa", "Baixa"), ("media", "Média"), ("alta", "Alta"), ("critica", "Crítica")]
    STATUS = [("aberta", "Aberta"), ("analise", "Em análise"), ("acao", "Ação em andamento"),
              ("verificacao", "Verificação de eficácia"), ("encerrada", "Encerrada")]
    titulo = models.CharField("título", max_length=200)
    origem = models.CharField(max_length=12, choices=ORIGENS, default="interna")
    gravidade = models.CharField(max_length=8, choices=GRAVIDADES, default="media")
    produto = models.ForeignKey("fiscal.Produto", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    fornecedor = models.ForeignKey("core.Fornecedor", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="nao_conformidades")
    cliente = models.ForeignKey("core.Cliente", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    descricao = models.TextField("descrição do problema")
    responsavel = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="responsável", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    prazo = models.DateField(null=True, blank=True)
    acao_imediata = models.TextField("ação imediata (contenção)", blank=True)
    causa_raiz = models.TextField("causa raiz", blank=True, help_text="Ex.: 5 porquês ou Ishikawa.")
    acao_corretiva = models.TextField("ação corretiva", blank=True)
    verificacao_eficacia = models.TextField("verificação de eficácia", blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="aberta", db_index=True)
    encerrada_em = models.DateField("encerrada em", null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "não conformidade"
        verbose_name_plural = "não conformidades"
        ordering = ["-numero"]

    def clean(self):
        if self.status == "encerrada":
            faltam = [rot for campo, rot in (("causa_raiz", "causa raiz"), ("acao_corretiva", "ação corretiva"),
                                             ("verificacao_eficacia", "verificação de eficácia"))
                      if not getattr(self, campo).strip()]
            if faltam:
                raise ValidationError(f"Para encerrar, preencha: {', '.join(faltam)}.")

    def save(self, *args, **kwargs):
        novo = self.pk is None
        if not self.prazo:
            dias = {"critica": 2, "alta": 7, "media": 15, "baixa": 30}[self.gravidade]
            self.prazo = timezone.localdate() + datetime.timedelta(days=dias)
        if not self.responsavel_id:
            self.responsavel = usuario_atual()
        if self.status == "encerrada" and not self.encerrada_em:
            self.encerrada_em = timezone.localdate()
        super().save(*args, **kwargs)
        if novo:
            self._abrir_tarefa()

    def _abrir_tarefa(self):
        from django.contrib.auth.models import Group
        from django.contrib.contenttypes.models import ContentType

        from apps.tarefas.models import Atividade

        Atividade.objects.create(
            titulo=f"{self.numero}: {self.titulo}"[:200], descricao=self.descricao, responsavel=self.responsavel,
            grupo=None if self.responsavel_id else Group.objects.filter(name="Qualidade").first(),
            prioridade={"critica": "urgente", "alta": "alta"}.get(self.gravidade, "media"), prazo=self.prazo,
            etiquetas="qualidade, não conformidade", content_type=ContentType.objects.get_for_model(self),
            object_id=self.pk)
