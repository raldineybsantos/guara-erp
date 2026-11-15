"""Os dashboards. Cada função recebe o Filtro e devolve a lista de blocos que o usuário pode ver."""
import datetime
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.db import models
from django.db.models import Avg, Count, DecimalField, DurationField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber, Movimentacao

from .blocos import aviso, brl, grafico, kpi, tabela
from .filtros import q_periodo, variacao

ZERO = Decimal(0)
MESES = "jan fev mar abr mai jun jul ago set out nov dez".split()
DEC = DecimalField(max_digits=20, decimal_places=4)


def rotulo_mes(d):
    return f"{MESES[d.month - 1]}/{d:%y}"


def _periodo(qs, campo, ini, fim):
    """Filtra o campo entre as datas; em campo de data e hora compara pela data (inclui o último dia)."""
    if "__" not in campo and isinstance(qs.model._meta.get_field(campo), models.DateTimeField):
        campo = f"{campo}__date"
    return qs.filter(q_periodo(campo, ini, fim))


def por_mes(qs, campo, filtro, valor=None):
    """Soma (ou conta) por mês do período. Devolve lista alinhada a filtro.meses."""
    agg = Sum(valor) if valor else Count("pk")
    out = {}
    for r in (_periodo(qs, campo, filtro.ini, filtro.fim).annotate(_m=TruncMonth(campo))
              .values("_m").annotate(t=agg)):
        m = r["_m"].date() if isinstance(r["_m"], datetime.datetime) else r["_m"]
        out[m] = r["t"] or 0
    return [out.get(m, 0) for m in filtro.meses]


def total(qs, campo, filtro, valor, anterior=False):
    ini, fim = (filtro.ant_ini, filtro.ant_fim) if anterior else (filtro.ini, filtro.fim)
    return _periodo(qs, campo, ini, fim).aggregate(t=Coalesce(Sum(valor), ZERO))["t"]


def _nota_empresa(filtro, itens):
    """Avisa quais quadros não são separados por empresa quando uma empresa específica foi escolhida."""
    if filtro.empresa:
        return aviso(f"Os quadros de {itens} somam todas as empresas: esses cadastros ainda não são separados "
                     f"por empresa.", "secondary")


def _resto(pares, topo):
    pares = sorted(pares, key=lambda p: -p[1])
    if len(pares) > topo:
        pares = pares[:topo] + [("Outros", sum((v for _, v in pares[topo:]), ZERO))]
    return pares


# ================================================================== financeiro
def _saldo_bancos(filtro):
    return sum((c.saldo_atual for c in filtro.escopo(ContaBancaria.objects.filter(ativa=True))), ZERO)


def _faixas(filtro, modelo):
    """Saldo em aberto por faixa de atraso (em dias) e por mês de vencimento futuro."""
    faixas = OrderedDict((k, ZERO) for k in ("A vencer", "1–30 dias", "31–60 dias", "61–90 dias", "> 90 dias"))
    for t in filtro.escopo(modelo.objects.em_aberto()):
        atraso = (filtro.hoje - t.vencimento).days
        k = ("A vencer" if atraso <= 0 else "1–30 dias" if atraso <= 30 else "31–60 dias" if atraso <= 60
             else "61–90 dias" if atraso <= 90 else "> 90 dias")
        faixas[k] += t.saldo
    return faixas


def financeiro(filtro):
    u, blocos = filtro.user, []
    ver_pagar, ver_receber = u.has_perm("financeiro.view_contapagar"), u.has_perm("financeiro.view_contareceber")
    mov = filtro.escopo(Movimentacao.objects.filter(transferencia=False))
    ent = total(mov.filter(tipo="entrada"), "data", filtro, "valor")
    sai = total(mov.filter(tipo="saida"), "data", filtro, "valor")
    ent_a = total(mov.filter(tipo="entrada"), "data", filtro, "valor", True)
    sai_a = total(mov.filter(tipo="saida"), "data", filtro, "valor", True)
    kpis = []
    if u.has_perm("financeiro.view_contabancaria"):
        kpis.append(kpi("Saldo em contas", _saldo_bancos(filtro), tom="info"))
    if u.has_perm("financeiro.view_movimentacao"):
        kpis += [kpi("Entradas (caixa)", ent, delta=variacao(ent, ent_a)),
                 kpi("Saídas (caixa)", sai, delta=variacao(sai, sai_a), bom="baixo"),
                 kpi("Resultado de caixa", ent - sai, delta=variacao(ent - sai, ent_a - sai_a))]
    faixa_r = faixa_p = None
    if ver_receber:
        faixa_r = _faixas(filtro, ContaReceber)
        aberto = sum(faixa_r.values(), ZERO)
        vencido = aberto - faixa_r["A vencer"]
        kpis += [kpi("A receber em aberto", aberto), kpi("Inadimplência", vencido / aberto * 100 if aberto else 0,
                                                         "pct", bom="baixo", dica=f"vencido: {brl(vencido)}")]
        recebidos = _periodo(filtro.escopo(ContaReceber.objects.filter(status="pago", data_pagamento__gte=F("data_emissao"))),
                             "data_pagamento", filtro.ini, filtro.fim)
        pagos = recebidos.aggregate(m=Avg(ExpressionWrapper(F("data_pagamento") - F("data_emissao"),
                                                            output_field=DurationField())))["m"]
        kpis.append(kpi("Prazo médio de recebimento", pagos.days + pagos.seconds / 86400 if pagos else None, "dias",
                        bom="baixo"))
    if ver_pagar:
        faixa_p = _faixas(filtro, ContaPagar)
        kpis.append(kpi("A pagar em aberto", sum(faixa_p.values(), ZERO), bom="baixo"))
        kpis.append(kpi("Vencido a pagar", sum(faixa_p.values(), ZERO) - faixa_p["A vencer"], bom="baixo",
                        tom="danger" if sum(faixa_p.values(), ZERO) - faixa_p["A vencer"] else ""))
    blocos += kpis
    if u.has_perm("financeiro.view_movimentacao"):
        blocos.append(grafico("Fluxo de caixa realizado", "bar", [rotulo_mes(m) for m in filtro.meses],
                              [("Entradas", por_mes(mov.filter(tipo="entrada"), "data", filtro, "valor")),
                               ("Saídas", por_mes(mov.filter(tipo="saida"), "data", filtro, "valor"))], largura=12))
        cats = (mov.filter(tipo="saida").filter(q_periodo("data", filtro.ini, filtro.fim))
                .values("plano_conta__nome").annotate(t=Sum("valor")))
        pares = _resto([(c["plano_conta__nome"] or "Sem categoria", c["t"]) for c in cats], 7)
        blocos.append(grafico("Despesas por categoria", "doughnut", [p[0] for p in pares], [("Despesas", [p[1] for p in pares])]))
        cc = (mov.filter(tipo="saida").filter(q_periodo("data", filtro.ini, filtro.fim))
              .values("centro_custo__nome").annotate(t=Sum("valor")).order_by("-t")[:10])
        blocos.append(tabela("Despesas por centro de custo", ["Centro de custo", "Total"],
                             [(c["centro_custo__nome"] or "Sem centro de custo", c["t"]) for c in cc], [None, "moeda"]))
    if faixa_r is not None or faixa_p is not None:
        series = []
        if faixa_r is not None:
            series.append(("A receber", list(faixa_r.values())))
        if faixa_p is not None:
            series.append(("A pagar", list(faixa_p.values())))
        blocos.append(grafico("Contas em aberto por atraso", "bar", list((faixa_r or faixa_p).keys()), series,
                              nota="Saldo em aberto hoje, por faixa de atraso."))
        prox = [_somar(filtro.hoje, i) for i in range(6)]
        series = []
        for rot, modelo, ok in (("A receber", ContaReceber, ver_receber), ("A pagar", ContaPagar, ver_pagar)):
            if ok:
                acc = defaultdict(lambda: ZERO)
                for t in filtro.escopo(modelo.objects.em_aberto()):
                    acc[max(t.vencimento, filtro.hoje).replace(day=1)] += t.saldo
                series.append((rot, [acc[m] for m in prox]))
        blocos.append(grafico("Vencimentos dos próximos meses", "bar", [rotulo_mes(m) for m in prox], series,
                              nota="Os títulos vencidos entram no mês atual."))
    if ver_receber:
        top = (filtro.escopo(ContaReceber.objects.em_aberto()).select_related("cliente"))
        acc = defaultdict(lambda: ZERO)
        for t in top:
            acc[t.cliente.nome if t.cliente_id else "Sem cliente"] += t.saldo
        blocos.append(tabela("Maiores saldos a receber", ["Cliente", "Em aberto"],
                             sorted(acc.items(), key=lambda p: -p[1])[:10], [None, "moeda"]))
    if ver_pagar:
        acc = defaultdict(lambda: ZERO)
        for t in filtro.escopo(ContaPagar.objects.em_aberto()).select_related("fornecedor"):
            acc[t.fornecedor.nome if t.fornecedor_id else "Sem fornecedor"] += t.saldo
        blocos.append(tabela("Maiores saldos a pagar", ["Fornecedor", "Em aberto"],
                             sorted(acc.items(), key=lambda p: -p[1])[:10], [None, "moeda"]))
    return blocos


def _somar(d, n):
    m = d.month - 1 + n
    return datetime.date(d.year + m // 12, m % 12 + 1, 1)


# ================================================================== comercial
def comercial(filtro):
    from apps.crm.models import ABERTOS, ESTAGIOS, PROBABILIDADE, Oportunidade
    from apps.vendas.models import ItemPedidoVenda, PedidoVenda

    u, blocos = filtro.user, []
    if u.has_perm("vendas.view_pedidovenda"):
        fat = filtro.escopo(PedidoVenda.objects.filter(status="faturado"))
        v, v_a = total(fat, "data_pedido", filtro, "valor_total"), total(fat, "data_pedido", filtro, "valor_total", True)
        n = fat.filter(q_periodo("data_pedido", filtro.ini, filtro.fim)).count()
        n_a = fat.filter(q_periodo("data_pedido", filtro.ant_ini, filtro.ant_fim)).count()
        blocos += [kpi("Faturamento", v, delta=variacao(v, v_a), tom="info"),
                   kpi("Pedidos faturados", n, "int", delta=variacao(n, n_a)),
                   kpi("Ticket médio", v / n if n else 0, delta=variacao(v / n if n else 0, v_a / n_a if n_a else 0)),
                   kpi("Bloqueados por crédito", filtro.escopo(PedidoVenda.objects.filter(status="bloqueado")).count(),
                       "int", bom="baixo")]
    if u.has_perm("crm.view_oportunidade"):
        abertas = Oportunidade.objects.filter(estagio__in=ABERTOS)
        tot = abertas.aggregate(t=Coalesce(Sum("valor"), ZERO))["t"]
        pond = sum((o.valor * PROBABILIDADE.get(o.estagio, 0) / 100 for o in abertas), ZERO)
        fech = _periodo(Oportunidade.objects.all(), "fechada_em", filtro.ini, filtro.fim)
        g, p = fech.filter(estagio="ganha").count(), fech.filter(estagio="perdida").count()
        blocos += [kpi("Pipeline em aberto", tot), kpi("Pipeline ponderado", pond),
                   kpi("Taxa de conversão", g / (g + p) * 100 if g + p else None, "pct",
                       dica=f"{g} ganha(s) e {p} perdida(s) no período")]
    if u.has_perm("vendas.view_pedidovenda"):
        blocos.append(grafico("Faturamento por mês", "bar", [rotulo_mes(m) for m in filtro.meses],
                              [("Faturamento", por_mes(fat, "data_pedido", filtro, "valor_total"))], largura=12))
        acc = (fat.filter(q_periodo("data_pedido", filtro.ini, filtro.fim)).values("cliente__nome")
               .annotate(t=Sum("valor_total"), n=Count("pk")).order_by("-t")[:10])
        blocos.append(tabela("Maiores clientes", ["Cliente", "Pedidos", "Faturado"],
                             [(a["cliente__nome"], a["n"], a["t"]) for a in acc], [None, "int", "moeda"]))
        liquido = ExpressionWrapper(F("quantidade") * F("valor_unitario") * (Value(100) - F("desconto_pct")) / Value(100),
                                    output_field=DEC)
        itens = (ItemPedidoVenda.objects.filter(pedido__in=fat.filter(q_periodo("data_pedido", filtro.ini, filtro.fim)))
                 .values("produto__descricao").annotate(t=Sum(liquido), q=Sum("quantidade")).order_by("-t")[:10])
        blocos.append(tabela("Produtos mais vendidos", ["Produto", "Quantidade", "Faturado"],
                             [(i["produto__descricao"], i["q"], i["t"]) for i in itens], [None, "num", "moeda"]))
        vend = (fat.filter(q_periodo("data_pedido", filtro.ini, filtro.fim)).values("vendedor__username")
                .annotate(t=Sum("valor_total"), n=Count("pk")).order_by("-t")[:10])
        blocos.append(tabela("Vendas por vendedor", ["Vendedor", "Pedidos", "Faturado"],
                             [(a["vendedor__username"] or "—", a["n"], a["t"]) for a in vend], [None, "int", "moeda"]))
    if u.has_perm("crm.view_oportunidade"):
        por = {e: ZERO for e, _ in ESTAGIOS if e in ABERTOS}
        for o in abertas:
            por[o.estagio] += o.valor
        nomes = dict(ESTAGIOS)
        blocos.append(grafico("Funil de vendas (em aberto)", "hbar", [nomes[e] for e in por],
                              [("Valor", list(por.values()))]))
        motivos = (_periodo(Oportunidade.objects.filter(estagio="perdida"), "fechada_em", filtro.ini, filtro.fim)
                   .values("motivo_perda").annotate(n=Count("pk")))
        pares = _resto([(m["motivo_perda"] or "Não informado", m["n"]) for m in motivos], 6)
        blocos.append(grafico("Motivos de perda", "doughnut", [p[0] for p in pares], [("Perdidas", [p[1] for p in pares])],
                              fmt="int"))
    nota = _nota_empresa(filtro, "oportunidades (CRM)")
    if nota and u.has_perm("crm.view_oportunidade"):
        blocos.insert(0, nota)
    return blocos


# ================================================================== suprimentos
VALIDOS = ("aprovado", "parcial", "recebido")


def suprimentos(filtro):
    from apps.compras.models import PedidoCompra
    from apps.estoque.models import Saldo
    from apps.fiscal.models import Produto

    u, blocos = filtro.user, []
    if u.has_perm("compras.view_pedidocompra"):
        ped = filtro.escopo(PedidoCompra.objects.filter(status__in=VALIDOS))
        v, v_a = total(ped, "data_pedido", filtro, "valor_total"), total(ped, "data_pedido", filtro, "valor_total", True)
        n = ped.filter(q_periodo("data_pedido", filtro.ini, filtro.fim)).count()
        blocos += [kpi("Comprado no período", v, delta=variacao(v, v_a), bom="baixo", tom="info"),
                   kpi("Pedidos aprovados", n, "int"),
                   kpi("Aguardando aprovação", filtro.escopo(PedidoCompra.objects.filter(status="em_aprovacao")).count(),
                       "int", bom="baixo"),
                   kpi("Recebimento pendente", filtro.escopo(PedidoCompra.objects.filter(status__in=("aprovado", "parcial")))
                       .count(), "int", bom="baixo")]
    if u.has_perm("estoque.view_saldo"):
        saldos = Saldo.objects.select_related("produto")
        valor = sum((s.quantidade * s.custo_medio for s in saldos), ZERO)
        por_prod = defaultdict(lambda: ZERO)
        for s in saldos:
            por_prod[s.produto_id] += s.quantidade
        abaixo = sum(1 for p in Produto.objects.filter(ativo=True, estoque_minimo__gt=0)
                     if por_prod.get(p.pk, ZERO) < p.estoque_minimo)
        blocos += [kpi("Valor em estoque", valor), kpi("Produtos abaixo do mínimo", abaixo, "int", bom="baixo",
                                                          tom="danger" if abaixo else "")]
    if u.has_perm("compras.view_pedidocompra"):
        blocos.append(grafico("Compras por mês", "bar", [rotulo_mes(m) for m in filtro.meses],
                              [("Comprado", por_mes(ped, "data_pedido", filtro, "valor_total"))], largura=12))
        no_per = filtro.escopo(PedidoCompra.objects.exclude(status="cancelado")).filter(
            q_periodo("data_pedido", filtro.ini, filtro.fim))
        nomes = dict(PedidoCompra.STATUS)
        st = no_per.values("status").annotate(n=Count("pk")).order_by("-n")
        blocos.append(grafico("Pedidos por situação", "doughnut", [nomes[s["status"]] for s in st],
                              [("Pedidos", [s["n"] for s in st])], fmt="int"))
        forn = (ped.filter(q_periodo("data_pedido", filtro.ini, filtro.fim)).values("fornecedor__nome")
                .annotate(t=Sum("valor_total"), n=Count("pk")).order_by("-t")[:10])
        blocos.append(tabela("Maiores fornecedores", ["Fornecedor", "Pedidos", "Comprado"],
                             [(f["fornecedor__nome"], f["n"], f["t"]) for f in forn], [None, "int", "moeda"]))
    if u.has_perm("estoque.view_saldo"):
        topo = sorted(((s.produto.descricao, s.quantidade, s.quantidade * s.custo_medio) for s in saldos
                       if s.quantidade > 0), key=lambda r: -r[2])[:10]
        blocos.append(tabela("Maior valor em estoque", ["Produto", "Quantidade", "Valor"], topo,
                             [None, "num", "moeda"]))
        nota = _nota_empresa(filtro, "estoque")
        if nota:
            blocos.insert(0, nota)
    return blocos


# ================================================================== operação
def operacao(filtro):
    from apps.manutencao.models import OrdemServico
    from apps.producao.models import OrdemProducao
    from apps.qualidade.models import NaoConformidade
    from apps.sst.models import Incidente

    u, blocos = filtro.user, []
    sem_empresa = []
    if u.has_perm("producao.view_ordemproducao"):
        ops = filtro.escopo(OrdemProducao.objects.all())
        ativas = ops.filter(status__in=("planejada", "liberada"))
        atrasadas = ativas.filter(data_prevista__lt=filtro.hoje).count()
        enc = ops.filter(status="encerrada")
        blocos += [kpi("OPs em andamento", ativas.count(), "int"),
                   kpi("OPs atrasadas", atrasadas, "int", bom="baixo", tom="danger" if atrasadas else ""),
                   kpi("Custo apurado (encerradas)", total(enc, "atualizado_em", filtro, "custo_total"))]
        nomes = dict(OrdemProducao.STATUS)
        st = ops.values("status").annotate(n=Count("pk"))
        blocos.append(grafico("Ordens de produção por situação", "doughnut", [nomes[s["status"]] for s in st],
                              [("Ordens", [s["n"] for s in st])], fmt="int"))
    if u.has_perm("manutencao.view_ordemservico"):
        sem_empresa.append("manutenção")
        os_ = OrdemServico.objects.all()
        per = os_.filter(abertura__date__gte=filtro.ini, abertura__date__lte=filtro.fim)
        corr = per.filter(tipo="corretiva").count()
        custo = sum((o.custo_total for o in per.filter(status="concluida")), ZERO)
        blocos += [kpi("OS abertas", os_.filter(status__in=("aberta", "execucao")).count(), "int", bom="baixo"),
                   kpi("Corretivas no período", corr, "int", bom="baixo",
                       dica=f"{corr / per.count() * 100:.0f}% das OS" if per.count() else ""),
                   kpi("Custo das OS concluídas", custo, bom="baixo")]
        nomes = dict(OrdemServico.TIPOS)
        tp = per.values("tipo").annotate(n=Count("pk"))
        blocos.append(grafico("OS por tipo (período)", "bar", [nomes[t["tipo"]] for t in tp],
                              [("OS", [t["n"] for t in tp])], fmt="int"))
    if u.has_perm("qualidade.view_naoconformidade"):
        sem_empresa.append("qualidade")
        nc = NaoConformidade.objects.exclude(status="encerrada")
        graves = nc.filter(gravidade__in=("alta", "critica")).count()
        blocos.append(kpi("Não conformidades abertas", nc.count(), "int", bom="baixo"))
        blocos.append(kpi("NCs alta ou crítica", graves, "int", bom="baixo", tom="danger" if graves else ""))
        nomes = dict(NaoConformidade.GRAVIDADES)
        gv = nc.values("gravidade").annotate(n=Count("pk"))
        blocos.append(grafico("NCs abertas por gravidade", "bar", [nomes[g["gravidade"]] for g in gv],
                              [("NCs", [g["n"] for g in gv])], fmt="int"))
    if u.has_perm("sst.ver_todos_incidente"):
        sem_empresa.append("SST")
        inc = Incidente.objects.filter(data_hora__date__gte=filtro.ini, data_hora__date__lte=filtro.fim)
        nomes = dict(Incidente.TIPOS)
        tp = inc.values("tipo").annotate(n=Count("pk"))
        blocos.append(kpi("Incidentes no período", inc.count(), "int", bom="baixo"))
        blocos.append(grafico("Incidentes por tipo", "bar", [nomes[t["tipo"]] for t in tp],
                              [("Incidentes", [t["n"] for t in tp])], fmt="int"))
    nota = _nota_empresa(filtro, ", ".join(sem_empresa)) if sem_empresa else None
    if nota:
        blocos.insert(0, nota)
    return blocos


# ================================================================== service desk
def servicedesk(filtro):
    from apps.servicedesk.models import Area, Chamado

    u = filtro.user
    areas = Area.objects.all() if u.is_superuser else Area.objects.filter(grupo__in=u.groups.all())
    base = Chamado.objects.filter(area__in=areas)
    per = base.filter(criado_em__date__gte=filtro.ini, criado_em__date__lte=filtro.fim)
    res = base.filter(resolvido_em__date__gte=filtro.ini, resolvido_em__date__lte=filtro.fim)
    no_prazo = res.filter(prazo_solucao__isnull=False, resolvido_em__lte=F("prazo_solucao")).count()
    com_prazo = res.filter(prazo_solucao__isnull=False).count()
    tempo = res.aggregate(m=Avg(ExpressionWrapper(F("resolvido_em") - F("criado_em"),
                                                  output_field=DurationField())))["m"]
    nota = res.filter(avaliacao__isnull=False).aggregate(m=Avg("avaliacao"))["m"]
    ia = res.filter(triagem_ia="resolvido").count()
    blocos = [kpi("Chamados abertos no período", per.count(), "int", delta=variacao(
                  per.count(), base.filter(criado_em__date__gte=filtro.ant_ini, criado_em__date__lte=filtro.ant_fim).count()),
                  bom="baixo"),
              kpi("Resolvidos no período", res.count(), "int"),
              kpi("Resolvidos dentro do SLA", no_prazo / com_prazo * 100 if com_prazo else None, "pct"),
              kpi("Tempo médio de solução", tempo.total_seconds() / 3600 if tempo else None, "horas", bom="baixo"),
              kpi("Satisfação média", nota, "num", dica="de 1 a 5"),
              kpi("Resolvidos pela IA", ia / res.count() * 100 if res.count() else None, "pct",
                  dica=f"{ia} chamado(s)"),
              kpi("Em andamento agora", base.filter(status__in=Chamado.ATIVOS).count(), "int", bom="baixo"),
              kpi("SLA vencido agora", base.filter(status__in=Chamado.ATIVOS, prazo_solucao__lt=timezone.now()).count(),
                  "int", bom="baixo", tom="danger")]
    blocos.append(grafico("Chamados abertos × resolvidos por mês", "bar", [rotulo_mes(m) for m in filtro.meses],
                          [("Abertos", por_mes(base, "criado_em", filtro)),
                           ("Resolvidos", por_mes(base.filter(resolvido_em__isnull=False), "resolvido_em", filtro))],
                          fmt="int", largura=12))
    nomes = dict(Chamado.STATUS)
    st = base.filter(status__in=Chamado.ATIVOS + ("aguardando_aprovacao",)).values("status").annotate(n=Count("pk"))
    blocos.append(grafico("Backlog por situação", "doughnut", [nomes[s["status"]] for s in st],
                          [("Chamados", [s["n"] for s in st])], fmt="int"))
    linhas = []
    for a in areas.order_by("nome"):
        r = res.filter(area=a)
        n = r.count()
        cp = r.filter(prazo_solucao__isnull=False).count()
        ok = r.filter(prazo_solucao__isnull=False, resolvido_em__lte=F("prazo_solucao")).count()
        m = r.aggregate(m=Avg(ExpressionWrapper(F("resolvido_em") - F("criado_em"),
                                                output_field=DurationField())))["m"]
        linhas.append((a.nome, per.filter(area=a).count(), n, ok / cp * 100 if cp else None,
                       m.total_seconds() / 3600 if m else None))
    blocos.append(tabela("Por área", ["Área", "Abertos", "Resolvidos", "No SLA", "Tempo médio"], linhas,
                         [None, "int", "int", "pct", "horas"], largura=12))
    srv = (per.values("servico__nome", "area__nome").annotate(n=Count("pk")).order_by("-n")[:10])
    blocos.append(tabela("Serviços mais pedidos", ["Serviço", "Área", "Chamados"],
                         [(s["servico__nome"], s["area__nome"], s["n"]) for s in srv], [None, None, "int"]))
    blocos.append(aviso("Os chamados não são separados por empresa: o filtro de empresa não se aplica a este painel.",
                        "secondary") if filtro.empresa else None)
    return [b for b in blocos if b]


# ================================================================== executivo
def executivo(filtro):
    u = filtro.user
    blocos = []
    fin = u.has_perm("financeiro.view_movimentacao")
    if fin:
        mov = filtro.escopo(Movimentacao.objects.filter(transferencia=False))
        ent = total(mov.filter(tipo="entrada"), "data", filtro, "valor")
        sai = total(mov.filter(tipo="saida"), "data", filtro, "valor")
        ent_a = total(mov.filter(tipo="entrada"), "data", filtro, "valor", True)
        sai_a = total(mov.filter(tipo="saida"), "data", filtro, "valor", True)
        if u.has_perm("financeiro.view_contabancaria"):
            blocos.append(kpi("Saldo em contas", _saldo_bancos(filtro), tom="info"))
        blocos += [kpi("Entradas (caixa)", ent, delta=variacao(ent, ent_a)),
                   kpi("Saídas (caixa)", sai, delta=variacao(sai, sai_a), bom="baixo"),
                   kpi("Resultado de caixa", ent - sai, delta=variacao(ent - sai, ent_a - sai_a))]
    if u.has_perm("financeiro.view_contareceber"):
        f = _faixas(filtro, ContaReceber)
        ab = sum(f.values(), ZERO)
        blocos.append(kpi("A receber em aberto", ab, dica=f"vencido: {kpi('', ab - f['A vencer'])['texto']}",
                          url="relatorios:financeiro"))
    if u.has_perm("financeiro.view_contapagar"):
        f = _faixas(filtro, ContaPagar)
        ab = sum(f.values(), ZERO)
        blocos.append(kpi("A pagar em aberto", ab, bom="baixo", dica=f"vencido: {kpi('', ab - f['A vencer'])['texto']}",
                          url="relatorios:financeiro"))
    if u.has_perm("vendas.view_pedidovenda"):
        from apps.vendas.models import PedidoVenda
        fat = filtro.escopo(PedidoVenda.objects.filter(status="faturado"))
        v, v_a = total(fat, "data_pedido", filtro, "valor_total"), total(fat, "data_pedido", filtro, "valor_total", True)
        blocos.append(kpi("Faturamento", v, delta=variacao(v, v_a), url="relatorios:comercial"))
    if u.has_perm("compras.view_pedidocompra"):
        from apps.compras.models import PedidoCompra
        ped = filtro.escopo(PedidoCompra.objects.filter(status__in=VALIDOS))
        v, v_a = total(ped, "data_pedido", filtro, "valor_total"), total(ped, "data_pedido", filtro, "valor_total", True)
        blocos.append(kpi("Comprado", v, delta=variacao(v, v_a), bom="baixo", url="relatorios:suprimentos"))
    if u.has_perm("crm.view_oportunidade"):
        from apps.crm.models import ABERTOS, Oportunidade
        blocos.append(kpi("Pipeline em aberto", Oportunidade.objects.filter(estagio__in=ABERTOS)
                          .aggregate(t=Coalesce(Sum("valor"), ZERO))["t"], url="relatorios:comercial"))
    if u.has_perm("servicedesk.change_chamado"):
        from apps.servicedesk.models import Area, Chamado
        areas = Area.objects.all() if u.is_superuser else Area.objects.filter(grupo__in=u.groups.all())
        b = Chamado.objects.filter(area__in=areas, status__in=Chamado.ATIVOS)
        blocos.append(kpi("Chamados em andamento", b.count(), "int", bom="baixo",
                          dica=f"{b.filter(prazo_solucao__lt=timezone.now()).count()} com SLA vencido",
                          url="relatorios:servicedesk"))
    if fin:
        blocos.append(grafico("Fluxo de caixa realizado", "bar", [rotulo_mes(m) for m in filtro.meses],
                              [("Entradas", por_mes(mov.filter(tipo="entrada"), "data", filtro, "valor")),
                               ("Saídas", por_mes(mov.filter(tipo="saida"), "data", filtro, "valor"))], largura=12))
    if u.has_perm("vendas.view_pedidovenda"):
        blocos.append(grafico("Faturamento por mês", "line", [rotulo_mes(m) for m in filtro.meses],
                              [("Faturamento", por_mes(fat, "data_pedido", filtro, "valor_total"))]))
    if u.has_perm("compras.view_pedidocompra"):
        blocos.append(grafico("Compras por mês", "line", [rotulo_mes(m) for m in filtro.meses],
                              [("Comprado", por_mes(ped, "data_pedido", filtro, "valor_total"))]))
    return blocos


DASHBOARDS = {
    "executivo": ("Visão executiva", "speedometer", executivo, None),
    "financeiro": ("Financeiro", "cash-stack", financeiro, ("financeiro.view_contapagar", "financeiro.view_contareceber",
                                                             "financeiro.view_movimentacao")),
    "comercial": ("Comercial", "graph-up-arrow", comercial, ("vendas.view_pedidovenda", "crm.view_oportunidade")),
    "suprimentos": ("Suprimentos", "truck", suprimentos, ("compras.view_pedidocompra", "estoque.view_saldo")),
    "operacao": ("Operação", "gear-wide-connected", operacao, ("producao.view_ordemproducao",
                                                               "manutencao.view_ordemservico",
                                                               "qualidade.view_naoconformidade",
                                                               "sst.ver_todos_incidente")),
    "servicedesk": ("Service Desk", "headset", servicedesk, ("servicedesk.change_chamado",)),
}
