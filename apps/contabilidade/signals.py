"""Razão único: os eventos dos outros módulos viram lançamentos no momento em que acontecem."""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.estoque.models import Movimento
from apps.core.models import notificar
from apps.financeiro.models import ContaPagar, ContaReceber, Movimentacao

from . import servicos


@receiver(post_save, sender=ContaPagar)
@receiver(post_save, sender=ContaReceber)
def _titulo(sender, instance, **kwargs):
    if not servicos.adiando():
        servicos.sincronizar_titulo(instance)


@receiver(post_save, sender=Movimentacao)
def _movimentacao(sender, instance, created, **kwargs):
    if created:
        servicos.baixa(instance)


@receiver(pre_delete, sender=Movimentacao)
def _movimentacao_excluida(sender, instance, **kwargs):
    servicos.estornar_de(instance, motivo="Baixa estornada.")


@receiver(post_save, sender=Movimento)
def _estoque(sender, instance, created, **kwargs):
    if created:
        servicos.movimento_estoque(instance)


@receiver(post_save, sender=ContaPagar)
def _orcamento(sender, instance, created, **kwargs):
    """Avisa o responsável quando um novo título faz a despesa passar do orçado."""
    if not created or instance.status == "cancelado":
        return
    from . import orcamento

    e = orcamento.estouro(instance)
    if e and e["orcamento"].responsavel_id:
        notificar([e["orcamento"].responsavel],
                  f"Orçamento estourado ({e['orcamento']}): {e['linha']} — orçado até o mês R$ {e['orcado']}, "
                  f"realizado R$ {e['realizado']}.", "")
