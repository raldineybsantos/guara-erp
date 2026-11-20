"""Gravação de lançamentos: manual (2 partidas) e automática por evento, com idempotência e estorno."""

import contextlib
import threading
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.middleware import usuario_atual

from .models import Lancamento, Partida, PeriodoFechado, RegraContabil

D2 = Decimal("0.01")
_estado = threading.local()


@contextlib.contextmanager
def provisao_adiada():
    """Compras e vendas criam o título e só depois acertam parcela e valor: dentro deste bloco o título
    não é contabilizado ao salvar; quem chamou faz `sincronizar_titulo` no fim."""
    _estado.adiar = True
    try:
        yield
    finally:
        _estado.adiar = False


def adiando():
    return getattr(_estado, "adiar", False)


EVENTOS_TITULO = ("titulo_pagar", "titulo_pagar_compra", "titulo_receber")


def empresa_da(referencia):
    """Empresa do documento que originou o lançamento (a do próprio registro ou a do pedido/recebimento ligado)."""
    for caminho in (("empresa",), ("recebimento", "empresa"), ("pedido", "empresa"), ("nota_fiscal", "empresa"),
                    ("armazem", "empresa")):
        obj = referencia
        for atributo in caminho:
            obj = getattr(obj, atributo, None)
            if obj is None:
                break
        else:
            return obj
    return None


@transaction.atomic
def gravar(data, historico, debito, credito, valor, evento="", referencia=None, centro_custo=None, manual=False,
           empresa=None):
    valor = Decimal(valor).quantize(D2)
    if valor <= 0:
        raise ValidationError("O valor do lançamento deve ser maior que zero.")
    if debito == credito:
        raise ValidationError("Débito e crédito não podem ser na mesma conta.")
    for c in (debito, credito):
        if c.sintetica:
            raise ValidationError(f"{c} é sintética e não recebe lançamentos.")
    if PeriodoFechado.fechado(data):
        if manual:
            raise ValidationError(f"O período {data:%m/%Y} está fechado.")
        original, data = data, PeriodoFechado.proxima_data_aberta(data)
        historico = f"{historico} (competência {original:%d/%m/%Y}; período fechado)"
    if empresa is None and referencia is not None:
        empresa = empresa_da(referencia)
    if empresa is None:
        from apps.core.middleware import empresa_atual
        from apps.core.models import Empresa

        empresa = empresa_atual() or Empresa.padrao()
    lanc = Lancamento(data=data, historico=historico[:250], evento=evento, criado_por=usuario_atual(),
                      empresa=empresa)
    if referencia is not None:
        lanc.content_type = ContentType.objects.get_for_model(referencia)
        lanc.object_id = referencia.pk
    lanc.save()
    Partida.objects.create(lancamento=lanc, conta=debito, tipo="D", valor=valor, centro_custo=centro_custo)
    Partida.objects.create(lancamento=lanc, conta=credito, tipo="C", valor=valor, centro_custo=centro_custo)
    return lanc


def ativos_de(referencia, eventos=None):
    qs = Lancamento.objects.filter(content_type=ContentType.objects.get_for_model(referencia),
                                   object_id=referencia.pk, estorno_de__isnull=True, estornado_por__isnull=True)
    return qs.filter(evento__in=eventos) if eventos else qs


def lancar(evento, valor, data, historico, referencia, centro_custo=None, debito=None, credito=None, empresa=None):
    """Lançamento automático pela regra do evento. Não repete se já existe um ativo para a referência."""
    valor = Decimal(valor or 0).quantize(D2)
    regra = RegraContabil.objects.filter(evento=evento, ativo=True).select_related("conta_debito",
                                                                                   "conta_credito").first()
    if regra is None or valor <= 0 or ativos_de(referencia, [evento]).exists():
        return None
    return gravar(data, historico, debito or regra.conta_debito, credito or regra.conta_credito, valor, evento,
                  referencia, centro_custo, empresa=empresa)


def estornar_de(referencia, eventos=None, motivo=""):
    n = 0
    for lanc in ativos_de(referencia, eventos):
        lanc.estornar(motivo=motivo)
        n += 1
    return n


def _conta_da_categoria(titulo):
    pc = titulo.plano_conta
    conta = getattr(pc, "conta_contabil", None) if pc else None
    return conta if conta and not conta.sintetica else None


def sincronizar_titulo(titulo):
    """Mantém a provisão do título igual ao que ele é hoje: lança, corrige (estorna e relança) ou estorna
    se foi cancelado. Chamado ao salvar o título e depois de ajustes feitos por compras e vendas."""
    receber = titulo._meta.model_name == "contareceber"
    if receber:
        evento = "titulo_receber"
    else:
        evento = "titulo_pagar_compra" if (titulo.observacoes or "").startswith("Pedido ") else "titulo_pagar"
    atual = ativos_de(titulo, EVENTOS_TITULO).first()
    if titulo.status == "cancelado":
        if atual:
            atual.estornar(motivo="Título cancelado.")
        return None
    valor = Decimal(titulo.valor).quantize(D2)
    if atual and atual.evento == evento and atual.valor == valor:
        return atual
    if atual:
        atual.estornar(motivo="Título alterado.")
    cat = _conta_da_categoria(titulo) if evento != "titulo_pagar_compra" else None
    parte = titulo.cliente if receber else titulo.fornecedor
    return lancar(evento, valor, titulo.competencia or titulo.data_emissao or timezone.localdate(),
                  f"{titulo} — {parte or 'sem cadastro'}", titulo, titulo.centro_custo,
                  debito=None if receber else cat, credito=cat if receber else None)


def baixa(mov):
    """Movimentação bancária: pagamento, recebimento ou avulsa. Transferências entre contas não contabilizam."""
    if getattr(mov, "guia_id", None):
        lancar("recolhimento_tributo", mov.valor, mov.data, mov.descricao, mov, mov.centro_custo)
        return
    titulo = mov.contapagar or mov.contareceber
    if titulo is None:
        if not getattr(mov, "transferencia", False):
            lancar("receita_avulsa" if mov.tipo == "entrada" else "despesa_avulsa", mov.valor, mov.data,
                   mov.descricao, mov, mov.centro_custo)
        return
    pagar = mov.contapagar_id is not None
    sincronizar_titulo(titulo)
    lancar("pagamento" if pagar else "recebimento", mov.valor, mov.data, mov.descricao, mov, mov.centro_custo)
    # Na baixa que quita o título, reconhece juros e descontos (a provisão foi feita pelo valor original).
    # O título é quitado pelo valor bruto: o dinheiro que saiu (mov.valor) é menor quando há tributo retido.
    bruto = getattr(mov, "_bruto", None)
    if bruto is None:
        bruto = mov.valor + (mov.retencoes_baixa.aggregate(t=Sum("valor"))["t"] or Decimal("0")
                             if mov.pk else Decimal("0"))
    if titulo.valor_pago + bruto >= titulo.valor_total:
        if titulo.juros:
            lancar("juros_pagos" if pagar else "juros_recebidos", titulo.juros, mov.data,
                   f"Juros/multa: {titulo}", mov, mov.centro_custo)
        if titulo.desconto:
            lancar("desconto_obtido" if pagar else "desconto_concedido", titulo.desconto, mov.data,
                   f"Desconto: {titulo}", mov, mov.centro_custo)


EVENTO_ESTOQUE = {("compra", "entrada"): "compra_estoque", ("venda", "saida"): "cmv",
                  ("consumo", "saida"): "consumo_estoque", ("manutencao", "saida"): "consumo_estoque",
                  ("epi", "saida"): "consumo_estoque", ("inventario", "entrada"): "ajuste_estoque_entrada",
                  ("ajuste", "entrada"): "ajuste_estoque_entrada", ("inventario", "saida"): "ajuste_estoque_saida",
                  ("ajuste", "saida"): "ajuste_estoque_saida"}


def movimento_estoque(mov):
    """Produção e transferências ficam dentro da conta Estoques e não geram lançamento."""
    evento = EVENTO_ESTOQUE.get((mov.origem, mov.tipo))
    if evento:
        lancar(evento, mov.valor, timezone.localtime(mov.data).date(),
               f"{mov.get_origem_display()}: {mov.produto} {mov.documento}".strip(), mov)


def escopo_lancamentos(qs, empresa=None, user=None, caminho="lancamento__empresa"):
    """Recorta partidas/lançamentos por empresa. Sem empresa escolhida, vale o acesso do usuário."""
    from apps.core.crud import restringir_por_empresa

    if empresa is not None:
        return qs.filter(**{caminho: empresa})
    if user is not None:
        return restringir_por_empresa(qs, qs.model, user, caminho)
    return qs


def saldos(ate=None, de=None, empresa=None, user=None, sem_encerramento=False):
    """{conta_id: (débitos, créditos)} das partidas no intervalo [de, ate].

    sem_encerramento: True ignora todos os lançamentos de encerramento do exercício (movimento "antes do
    encerramento", como na DRE); um número (ano) ignora só o encerramento daquele ano."""
    from django.db.models import Q, Sum

    qs = escopo_lancamentos(Partida.objects.all(), empresa, user)
    if sem_encerramento is True:
        qs = qs.exclude(lancamento__evento="encerramento_exercicio")
    elif sem_encerramento:
        qs = qs.exclude(lancamento__evento="encerramento_exercicio", lancamento__data__year=sem_encerramento)
    if de:
        qs = qs.filter(lancamento__data__gte=de)
    if ate:
        qs = qs.filter(lancamento__data__lte=ate)
    linhas = qs.values("conta_id").annotate(d=Sum("valor", filter=Q(tipo="D")), c=Sum("valor", filter=Q(tipo="C")))
    return {x["conta_id"]: (x["d"] or Decimal(0), x["c"] or Decimal(0)) for x in linhas}


def saldo_natural(conta, deb, cred):
    return deb - cred if conta.devedora else cred - deb


