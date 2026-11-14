"""Contabiliza o que já existia antes de o módulo ser instalado (ou enquanto uma regra estava desligada).
Pode ser rodado mais de uma vez: o que já tem lançamento é pulado."""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Gera os lançamentos contábeis de títulos, baixas e movimentos de estoque ainda não contabilizados."

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.contabilidade import servicos
        from apps.contabilidade.models import Lancamento
        from apps.estoque.models import Movimento
        from apps.financeiro.models import ContaPagar, ContaReceber, Movimentacao

        antes = Lancamento.objects.count()
        for m in Movimento.objects.order_by("data", "id"):
            servicos.movimento_estoque(m)
        for modelo in (ContaPagar, ContaReceber):
            for t in modelo.objects.order_by("data_emissao", "id"):
                servicos.sincronizar_titulo(t)
        for mov in Movimentacao.objects.select_related("contapagar", "contareceber").order_by("data", "id"):
            if not servicos.ativos_de(mov).exists():
                # na reconstrução, os juros/descontos entram na última baixa do título
                titulo = mov.contapagar or mov.contareceber
                if titulo is not None:
                    ultima = not titulo.movimentacoes.filter(pk__gt=mov.pk).exists()
                    quitado = titulo.status == "pago" and ultima
                    titulo.valor_pago = titulo.valor_pago - mov.valor if quitado else 0
                servicos.baixa(mov)
        self.stdout.write(f"{Lancamento.objects.count() - antes} lançamento(s) gerado(s).")
