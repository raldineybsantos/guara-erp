"""Encerramento do exercício: zera as contas de resultado do ano contra "Lucros ou prejuízos acumulados" (2.3.2).

É um lançamento de várias partidas em 31/12, que se estorna como os demais. A DRE ignora esse lançamento (mostra o
resultado do ano), e o balanço o inclui (o resultado passa a ficar no patrimônio líquido)."""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.middleware import usuario_atual
from apps.core.models import Empresa

from . import servicos
from .models import ContaContabil, Lancamento, PeriodoFechado, Partida

ZERO = Decimal("0")
EVENTO = "encerramento_exercicio"
CODIGO_LUCROS = "2.3.2"
RESULTADO = ("receita", "deducao", "custo", "despesa")


def encerramento_do_ano(ano, empresa):
    return (Lancamento.objects.filter(evento=EVENTO, data__year=ano, empresa=empresa, estorno_de__isnull=True,
                                      estornado_por__isnull=True).first())


def resultados_do_ano(ano, empresa):
    """[(conta, saldo devedor)] das contas de resultado com saldo no ano, antes do encerramento (credor = negativo)."""
    mov = servicos.saldos(de=datetime.date(ano, 1, 1), ate=datetime.date(ano, 12, 31), empresa=empresa,
                          sem_encerramento=True)
    linhas = []
    for c in ContaContabil.objects.filter(natureza__in=RESULTADO, sintetica=False).order_by("codigo"):
        d, cr = mov.get(c.pk, (ZERO, ZERO))
        if d - cr:
            linhas.append((c, d - cr))
    return linhas


@transaction.atomic
def encerrar_exercicio(ano, empresa=None):
    empresa = empresa or Empresa.padrao()
    if empresa is None:
        raise ValidationError("Cadastre a empresa antes de encerrar o exercício.")
    data = datetime.date(ano, 12, 31)
    if encerramento_do_ano(ano, empresa):
        raise ValidationError(f"O exercício {ano} já foi encerrado. Estorne o lançamento de encerramento para refazer.")
    if PeriodoFechado.fechado(data):
        raise ValidationError(f"O período 12/{ano} está fechado. Reabra-o para encerrar o exercício.")
    lucros = ContaContabil.objects.filter(codigo=CODIGO_LUCROS, sintetica=False).first()
    if lucros is None:
        raise ValidationError(f"A conta {CODIGO_LUCROS} (lucros ou prejuízos acumulados) não existe no plano de contas.")
    linhas = resultados_do_ano(ano, empresa)
    if not linhas:
        raise ValidationError(f"Não há resultado a apurar em {ano}.")
    lanc = Lancamento.objects.create(data=data, historico=f"Encerramento do exercício {ano}: apuração do resultado",
                                     evento=EVENTO, empresa=empresa, criado_por=usuario_atual())
    total_dev = ZERO  # soma dos saldos devedores zerados; negativo = lucro (credores maiores)
    for conta, v in linhas:
        # zera a conta: saldo devedor vai a crédito e saldo credor, a débito
        Partida.objects.create(lancamento=lanc, conta=conta, tipo="C" if v > 0 else "D", valor=abs(v))
        total_dev += v
    if total_dev < 0:      # lucro: credita lucros acumulados
        Partida.objects.create(lancamento=lanc, conta=lucros, tipo="C", valor=-total_dev)
    elif total_dev > 0:    # prejuízo
        Partida.objects.create(lancamento=lanc, conta=lucros, tipo="D", valor=total_dev)
    return lanc
