"""Qualidade: inspeção de recebimento (quarentena) e não conformidades com causa e ação corretiva."""

import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.middleware import usuario_atual
from apps.core.models import ModeloBase
from apps.core.numeracao import NumeradoMixin
from apps.core.formatos import qtd as fq


class Inspecao(NumeradoMixin, ModeloBase):
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

    @transaction.atomic
    def concluir(self, usuario, quantidade_aprovada, resultado):
        """Libera o aprovado para o armazém principal; o reprovado fica na quarentena e abre não conformidade."""
        from apps.estoque import models as est

        if self.status != "pendente":
            raise ValidationError("Inspeção já concluída.")
        aprovada = min(max(quantidade_aprovada, 0), self.quantidade)
        if aprovada > 0:
            est.transferir(self.produto, aprovada, self.armazem, est.Armazem.principal(), self.numero,
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


class NaoConformidade(NumeradoMixin, ModeloBase):
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
