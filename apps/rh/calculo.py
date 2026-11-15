"""Cálculos trabalhistas puros (sem banco): INSS, IRRF, salário-família, férias, aviso prévio."""
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
ZERO = D("0")


def q2(valor):
    return D(valor).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def inss(base, faixas):
    """INSS progressivo: cada alíquota incide só sobre a parcela dentro da faixa; a última faixa é o teto."""
    if not faixas:
        return ZERO
    base = min(D(base), faixas[-1][0])
    anterior, total = ZERO, ZERO
    for ate, aliquota in faixas:
        if base <= anterior:
            break
        total += (min(base, ate) - anterior) * aliquota / 100
        anterior = ate
    return q2(total)


def teto_inss(faixas):
    return faixas[-1][0] if faixas else ZERO


def irrf(rendimentos, inss_pago, dependentes, p, faixas, pensao=ZERO, aplicar_reducao=True):
    """IRRF mensal. Usa o que for mais vantajoso entre as deduções legais e o desconto simplificado e, a partir
    de 2026, a redução da Lei 15.270/2025 (calculada sobre os rendimentos tributáveis). Devolve (imposto, base)."""
    rendimentos = D(rendimentos)
    legal = rendimentos - D(inss_pago) - dependentes * p.deducao_dependente - D(pensao)
    simples = rendimentos - p.desconto_simplificado
    base = max(min(legal, simples), ZERO)
    imposto = ZERO
    for ate, aliquota, deduzir in faixas:
        if ate is None or base <= ate:
            imposto = max(base * aliquota / 100 - deduzir, ZERO)
            break
    imposto = q2(imposto)
    if aplicar_reducao and p.reducao_ate and imposto > 0:
        if rendimentos <= p.reducao_ate:
            imposto = max(imposto - p.reducao_maxima, ZERO)
        elif rendimentos <= p.reducao_limite:
            imposto = max(imposto - max(p.reducao_fixo - p.reducao_fator * rendimentos, ZERO), ZERO)
    return q2(imposto), q2(base)


def salario_familia(remuneracao, filhos, p):
    if filhos <= 0 or D(remuneracao) > p.familia_limite:
        return ZERO
    return q2(p.familia_cota * filhos)


def direito_ferias(faltas_injustificadas):
    """CLT art. 130: dias de férias conforme as faltas injustificadas no período aquisitivo."""
    if faltas_injustificadas <= 5:
        return 30
    if faltas_injustificadas <= 14:
        return 24
    if faltas_injustificadas <= 23:
        return 18
    if faltas_injustificadas <= 32:
        return 12
    return 0


def ferias(salario, dias, abono_dias=0):
    """Valores brutos das férias: gozo + 1/3 e, se houver, abono pecuniário + 1/3 (este indenizatório)."""
    dia = D(salario) / 30
    gozo = q2(dia * dias)
    abono = q2(dia * abono_dias)
    return {"ferias": gozo, "terco": q2(gozo / 3), "abono": abono, "terco_abono": q2(abono / 3)}


def dias_aviso(anos_completos):
    """Lei 12.506/2011: 30 dias + 3 por ano completo, até 90."""
    return min(30 + 3 * max(anos_completos, 0), 90)


def meses_avos(inicio, fim):
    """Avos (1/12) ganhos entre duas datas: mês com 15 dias ou mais de trabalho conta."""
    import calendar

    avos, ano, mes = 0, inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month):
        ultimo = calendar.monthrange(ano, mes)[1]
        ini = max(inicio, inicio.replace(year=ano, month=mes, day=1))
        f = min(fim, inicio.replace(year=ano, month=mes, day=ultimo))
        if (f - ini).days + 1 >= 15:
            avos += 1
        mes += 1
        if mes == 13:
            ano, mes = ano + 1, 1
    return avos
