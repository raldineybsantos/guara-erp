"""Folha de pagamento: cálculo mensal e de 13º, importação do ponto, fechamento (contas a pagar) e rescisão."""
import calendar
import datetime
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Colaborador

from . import calculo
from .calculo import D, ZERO, q2
from .models import (ApuracaoDia, Folha, Holerite, ItemHolerite, LancamentoFolha, ParametroFolha, PeriodoAquisitivo,
                     Rubrica, SolicitacaoFerias, rotulo_competencia)

REGIMES_SEM_PATRONAL = {"simples", "mei"}


def _rubrica(codigo):
    try:
        return Rubrica.objects.get(codigo=codigo)
    except Rubrica.DoesNotExist:
        raise ValidationError(f"Rubrica {codigo} não cadastrada. Rode `python manage.py setup_erp`.") from None


def _dados(colab):
    try:
        return colab.dados_trabalhistas
    except ObjectDoesNotExist:
        return None


def elegiveis(folha):
    """Colaboradores da empresa que têm remuneração na competência."""
    inicio = folha.competencia
    fim = inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])
    qs = Colaborador.objects.filter(empresa=folha.empresa, data_admissao__lte=fim,
                                    tipo_contrato__in=["clt", "temporario", "estagio", "socio"])
    return qs.exclude(data_desligamento__lt=inicio).select_related("dados_trabalhistas__jornada")


def _dias_trabalhados(colab, inicio, fim):
    """Dias comerciais (base 30) do mês em que o colaborador esteve contratado."""
    ini = max(colab.data_admissao, inicio)
    f = min(colab.data_desligamento or fim, fim)
    if ini == inicio and f == fim:
        return 30
    return max(min((f - ini).days + 1, 30), 0)


def _dias_ferias_no_mes(colab, inicio, fim):
    dias = 0
    for s in SolicitacaoFerias.objects.filter(colaborador=colab, status__in=["aprovada", "gozada"]):
        ini, f = max(s.inicio, inicio), min(s.fim, fim)
        if ini <= f:
            dias += (f - ini).days + 1
    return min(dias, 30)


def _ferias_pagas_no_mes(colab, inicio, fim):
    """Férias cujo pagamento (2 dias antes do início do gozo) cai neste mês."""
    out = []
    for s in SolicitacaoFerias.objects.filter(colaborador=colab, status__in=["aprovada", "gozada"]):
        pagamento = s.inicio - datetime.timedelta(days=2)
        if inicio <= pagamento <= fim:
            out.append(s)
    return out


class _Itens:
    """Acumula os itens do holerite e as bases de incidência."""

    def __init__(self):
        self.itens, self.base_inss, self.base_irrf, self.base_fgts = [], ZERO, ZERO, ZERO
        self.proventos, self.descontos = ZERO, ZERO

    def add(self, codigo, descricao, tipo, valor, referencia="", inss=False, irrf=False, fgts=False):
        valor = q2(valor)
        if valor == 0 and tipo != "informativo":
            return
        self.itens.append(dict(codigo=codigo, descricao=descricao, tipo=tipo, referencia=referencia, valor=valor))
        if tipo == "provento":
            self.proventos += valor
            self.base_inss += valor if inss else ZERO
            self.base_irrf += valor if irrf else ZERO
            self.base_fgts += valor if fgts else ZERO
        elif tipo == "desconto":
            self.descontos += valor
            # um desconto de rubrica que incide reduz as bases (ex.: faltas)
            self.base_inss -= valor if inss else ZERO
            self.base_irrf -= valor if irrf else ZERO
            self.base_fgts -= valor if fgts else ZERO


def _calcular_mensal(folha, colab, p, faixas_inss, faixas_irrf):
    inicio = folha.competencia
    fim = inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])
    it = _Itens()
    contrato = colab.tipo_contrato
    estagio, socio = contrato == "estagio", contrato == "socio"
    dias = _dias_trabalhados(colab, inicio, fim)
    dias_ferias = _dias_ferias_no_mes(colab, inicio, fim)
    dias_pagos = max(dias - dias_ferias, 0)
    base = colab.salario
    salario = q2(base / 30 * dias_pagos) if dias_pagos < 30 else q2(base)
    rotulo_sal = "Pró-labore" if socio else ("Bolsa-auxílio" if estagio else "Salário")
    it.add("0001", rotulo_sal, "provento", salario, f"{dias_pagos} dias",
           inss=not estagio, irrf=not estagio, fgts=not estagio and not socio)
    # férias pagas neste mês
    for s in _ferias_pagas_no_mes(colab, inicio, fim):
        v = calculo.ferias(base, s.dias, s.abono_dias)
        it.add("0010", f"Férias ({s.dias} dias)", "provento", v["ferias"], f"{s.inicio:%d/%m}", inss=True, irrf=True,
               fgts=True)
        it.add("0011", "1/3 constitucional de férias", "provento", v["terco"], inss=True, irrf=True, fgts=True)
        it.add("0012", f"Abono pecuniário ({s.abono_dias} dias)", "provento", v["abono"])
        it.add("0013", "1/3 sobre o abono pecuniário", "provento", v["terco_abono"])
    # lançamentos variáveis (manuais ou do ponto)
    for lan in LancamentoFolha.objects.filter(colaborador=colab, competencia=inicio).select_related("rubrica"):
        r = lan.rubrica
        it.add(r.codigo, r.nome, r.tipo, lan.valor, lan.referencia, inss=r.incide_inss, irrf=r.incide_irrf,
               fgts=r.incide_fgts)
    dep = list(colab.dependentes.all())
    n_irrf = sum(1 for d in dep if d.deduz_irrf)
    # salário-família (informa o direito; não incide)
    if not estagio and not socio:
        filhos = sum(1 for d in dep if d.tem_salario_familia(fim))
        it.add("0020", "Salário-família", "provento", calculo.salario_familia(it.base_inss, filhos, p), f"{filhos} filho(s)")
    # INSS
    bruto_inss = max(it.base_inss, ZERO)
    if estagio:
        inss = ZERO
    elif socio:
        teto = calculo.teto_inss(faixas_inss)
        inss = q2(min(bruto_inss, teto) * p.prolabore_inss / 100)
    else:
        inss = calculo.inss(bruto_inss, faixas_inss)
    it.add("9001", "INSS", "desconto", inss, "")
    # pensão alimentícia (% sobre o líquido legal)
    dados = _dados(colab)
    pensao = ZERO
    if dados and dados.pensao_percentual:
        pensao = q2((it.base_irrf - inss) * dados.pensao_percentual / 100)
        it.add("9003", "Pensão alimentícia", "desconto", pensao, f"{dados.pensao_percentual}%")
    # IRRF
    if estagio:
        irrf_v, base_irrf = ZERO, ZERO
    else:
        irrf_v, base_irrf = calculo.irrf(max(it.base_irrf, ZERO), inss, n_irrf, p, faixas_irrf, pensao)
    it.add("9002", "IRRF", "desconto", irrf_v)
    # benefícios com desconto do colaborador
    for bc in colab.beneficios.select_related("beneficio"):
        if not bc.vigente_em(fim):
            continue
        b = bc.beneficio
        desc = b.desconto_fixo + q2(base * b.desconto_percentual / 100)
        it.add("9300", f"Desconto {b.nome}", "desconto", desc)
    if dados and dados.vale_transporte:
        it.add("9010", "Desconto vale-transporte (6%)", "desconto", min(q2(base * D("0.06")), dados.vale_transporte))
    # FGTS e encargos patronais (informativos)
    fgts_base = max(it.base_fgts, ZERO)
    fgts = ZERO
    if not socio:
        aliq = p.fgts_aprendiz if (estagio or (dados and dados.categoria_esocial == "103")) else p.fgts_aliquota
        fgts = q2(fgts_base * aliq / 100) if not estagio else ZERO
    patronal = ZERO
    if folha.empresa.regime_tributario not in REGIMES_SEM_PATRONAL and not estagio:
        aliq_pat = p.patronal_aliquota if socio else p.patronal_aliquota + p.rat + p.terceiros
        patronal = q2(bruto_inss * aliq_pat / 100)
    if fgts:
        it.add("8001", "FGTS (depósito da empresa)", "informativo", fgts)
    liquido = it.proventos - it.descontos
    return it, dict(salario_base=base, bruto=it.proventos, base_inss=bruto_inss, inss=inss, base_irrf=base_irrf,
                    irrf=irrf_v, fgts=fgts, patronal=patronal, descontos=it.descontos, liquido=q2(liquido))


def _avos_13(colab, ano, ate):
    ini = max(colab.data_admissao, datetime.date(ano, 1, 1))
    fim = min(colab.data_desligamento or ate, ate)
    return calculo.meses_avos(ini, fim) if ini <= fim else 0


def _calcular_13(folha, colab, p, faixas_inss, faixas_irrf):
    ano, mes = folha.competencia.year, folha.competencia.month
    ate = datetime.date(ano, 12, 31)
    avos = _avos_13(colab, ano, ate)
    total = q2(colab.salario / 12 * avos)
    it = _Itens()
    estagio = colab.tipo_contrato == "estagio"
    if folha.tipo == "13_1":
        primeira = q2(total / 2)
        it.add("0030", "13º salário — 1ª parcela", "provento", primeira, f"{avos}/12")
        return it, dict(salario_base=colab.salario, bruto=primeira, liquido=primeira, descontos=ZERO, base_inss=ZERO,
                        inss=ZERO, base_irrf=ZERO, irrf=ZERO, fgts=ZERO, patronal=ZERO)
    # 2ª parcela: apura impostos sobre o total
    it.add("0031", "13º salário (integral)", "provento", total, f"{avos}/12", inss=True, irrf=True, fgts=True)
    adiantado = Holerite.objects.filter(
        folha__empresa=folha.empresa, folha__tipo="13_1", folha__competencia__year=ano, colaborador=colab).first()
    adiant = adiantado.liquido if adiantado else q2(total / 2)
    inss = ZERO if estagio else calculo.inss(total, faixas_inss)
    dep = sum(1 for d in colab.dependentes.all() if d.deduz_irrf)
    irrf_v, base_irrf = (ZERO, ZERO) if estagio else calculo.irrf(total, inss, dep, p, faixas_irrf, aplicar_reducao=False)
    it.add("9101", "(-) 1ª parcela adiantada", "desconto", adiant)
    it.add("9001", "INSS sobre 13º", "desconto", inss)
    it.add("9002", "IRRF sobre 13º", "desconto", irrf_v)
    fgts = ZERO if estagio else q2(total * p.fgts_aliquota / 100)
    patronal = ZERO
    if folha.empresa.regime_tributario not in REGIMES_SEM_PATRONAL and not estagio:
        patronal = q2(total * (p.patronal_aliquota + p.rat + p.terceiros) / 100)
    if fgts:
        it.add("8001", "FGTS (depósito da empresa)", "informativo", fgts)
    return it, dict(salario_base=colab.salario, bruto=total, base_inss=total, inss=inss, base_irrf=base_irrf,
                    irrf=irrf_v, fgts=fgts, patronal=patronal, descontos=it.descontos, liquido=q2(total - it.descontos))


@transaction.atomic
def calcular(folha):
    """Recalcula todos os holerites da folha (apaga e refaz). Folha fechada não é recalculada."""
    if folha.status == "fechada":
        raise ValidationError("A folha está fechada. Reabra para recalcular.")
    p = ParametroFolha.vigente(folha.competencia)
    faixas_inss, faixas_irrf = p.faixas_inss(), p.faixas_irrf()
    if not faixas_inss or not faixas_irrf:
        raise ValidationError("A tabela legal vigente está sem faixas de INSS ou IRRF.")
    folha.holerites.all().delete()
    tot = dict(bruto=ZERO, descontos=ZERO, liquido=ZERO, inss=ZERO, irrf=ZERO, fgts=ZERO, patronal=ZERO)
    for colab in elegiveis(folha):
        if folha.tipo == "mensal":
            it, r = _calcular_mensal(folha, colab, p, faixas_inss, faixas_irrf)
        else:
            it, r = _calcular_13(folha, colab, p, faixas_inss, faixas_irrf)
        if not it.itens:
            continue
        if r["liquido"] < 0:
            raise ValidationError(f"{colab}: os descontos superam os proventos (líquido {r['liquido']}). "
                                  "Revise os lançamentos.")
        h = Holerite.objects.create(folha=folha, colaborador=colab, **r)
        ItemHolerite.objects.bulk_create([ItemHolerite(holerite=h, **i) for i in it.itens])
        for k, v in (("bruto", r["bruto"]), ("descontos", r["descontos"]), ("liquido", r["liquido"]),
                     ("inss", r["inss"]), ("irrf", r["irrf"]), ("fgts", r["fgts"]), ("patronal", r["patronal"])):
            tot[k] += v
    folha.total_bruto, folha.total_descontos, folha.total_liquido = tot["bruto"], tot["descontos"], tot["liquido"]
    folha.total_inss, folha.total_irrf, folha.total_fgts = tot["inss"], tot["irrf"], tot["fgts"]
    folha.total_patronal = tot["patronal"]
    folha.status = "calculada"
    folha.save()
    return folha


def importar_ponto(folha):
    """Cria lançamentos de horas extras e faltas a partir da apuração do ponto no mês (jornadas sem banco de horas)."""
    from . import ponto

    inicio = folha.competencia
    fim = inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])
    r_extra, r_falta = _rubrica("0100"), _rubrica("0200")
    LancamentoFolha.objects.filter(competencia=inicio, origem="ponto", colaborador__empresa=folha.empresa).delete()
    criados = 0
    for colab in elegiveis(folha):
        dados = _dados(colab)
        j = dados.jornada if dados else None
        if j is None:
            continue
        ponto.apurar_periodo(colab, inicio, min(fim, timezone.localdate()))
        ap = ApuracaoDia.objects.filter(colaborador=colab, data__range=(inicio, fim))
        valor_hora = colab.salario / j.divisor_horas
        faltas = ap.filter(falta=True).count()
        if faltas:
            LancamentoFolha.objects.create(colaborador=colab, competencia=inicio, rubrica=r_falta, origem="ponto",
                                           valor=q2(colab.salario / 30 * faltas), referencia=f"{faltas} dia(s)")
            criados += 1
        if not j.banco_horas:
            extras = sum(a.saldo_min for a in ap.filter(falta=False) if a.saldo_min > 0)
            if extras:
                valor = q2(valor_hora * (1 + j.adicional_extra / 100) * D(extras) / 60)
                LancamentoFolha.objects.create(colaborador=colab, competencia=inicio, rubrica=r_extra,
                                               origem="ponto", valor=valor, referencia=f"{extras // 60}h{extras % 60:02d}")
                criados += 1
    return criados


@transaction.atomic
def fechar(folha, usuario):
    """Fecha a folha e gera as contas a pagar: salários líquidos, INSS, FGTS e IRRF."""
    from apps.financeiro.models import ContaPagar

    if folha.status != "calculada":
        raise ValidationError("Calcule a folha antes de fechar.")
    p = ParametroFolha.vigente(folha.competencia)
    ano, mes = folha.competencia.year, folha.competencia.month
    prox = datetime.date(ano + (mes == 12), mes % 12 + 1, 1)
    comp = rotulo_competencia(folha.competencia)

    def venc(dia):
        return prox.replace(day=min(dia, calendar.monthrange(prox.year, prox.month)[1]))

    if folha.tipo == "13_1":
        vencimentos = [("13º salário (1ª parcela)", folha.total_liquido, datetime.date(ano, 11, 30))]
    elif folha.tipo == "13_2":
        vencimentos = [("13º salário (2ª parcela)", folha.total_liquido, datetime.date(ano, 12, 20)),
                       ("INSS do 13º (colaboradores + patronal)", folha.total_inss + folha.total_patronal,
                        datetime.date(ano + 1, 1, 20)),
                       ("FGTS do 13º", folha.total_fgts, datetime.date(ano + 1, 1, 20)),
                       ("IRRF do 13º (DARF 0561)", folha.total_irrf, datetime.date(ano + 1, 1, 20))]
    else:
        vencimentos = [("Salários líquidos", folha.total_liquido, venc(p.dia_pagamento)),
                       ("INSS (colaboradores + patronal)", folha.total_inss + folha.total_patronal, venc(p.dia_guias)),
                       ("FGTS (guia)", folha.total_fgts, venc(p.dia_guias)),
                       ("IRRF (DARF 0561)", folha.total_irrf, venc(p.dia_guias))]
    ids = []
    for nome, valor, venc_ in vencimentos:
        if valor and valor > 0:
            t = ContaPagar.objects.create(
                empresa=folha.empresa, descricao=f"Folha {comp} — {nome}", documento=f"FOLHA {folha.competencia:%Y-%m}",
                valor=valor, vencimento=venc_, competencia=folha.competencia, data_emissao=timezone.localdate(),
                aprovado=True, observacoes="Gerada pelo fechamento da folha de pagamento.")
            ids.append(t.pk)
    folha.titulos_ids, folha.status = ids, "fechada"
    folha.fechada_em, folha.fechada_por = timezone.now(), usuario
    folha.save()
    return ids


@transaction.atomic
def reabrir(folha):
    from apps.financeiro.models import ContaPagar

    if folha.status != "fechada":
        raise ValidationError("A folha não está fechada.")
    titulos = ContaPagar.objects.filter(pk__in=folha.titulos_ids)
    if titulos.exclude(status="aberto").exists():
        raise ValidationError("Há contas desta folha já pagas ou baixadas: estorne os pagamentos antes de reabrir.")
    titulos.update(status="cancelado")
    folha.status, folha.titulos_ids, folha.fechada_em, folha.fechada_por = "calculada", [], None, None
    folha.save()
    return folha


# --------------------------------------------------------------------------- rescisão
def _anos_completos(ini, fim):
    return fim.year - ini.year - ((fim.month, fim.day) < (ini.month, ini.day))


def calcular_rescisao(r):
    """Estimativa gerencial das verbas rescisórias (TRCT). Confira com a contabilidade/sindicato antes de pagar."""
    colab = r.colaborador
    p = ParametroFolha.vigente(r.data_desligamento)
    faixas_inss, faixas_irrf = p.faixas_inss(), p.faixas_irrf()
    sal, dia = colab.salario, colab.salario / 30
    desl, adm = r.data_desligamento, colab.data_admissao
    linhas = []  # (descricao, tipo, valor)

    def add(desc, tipo, valor):
        valor = q2(valor)
        if valor:
            linhas.append({"descricao": desc, "tipo": tipo, "valor": str(valor)})
        return valor

    dias_mes = min(desl.day, 30)
    saldo = add(f"Saldo de salário ({dias_mes} dias)", "provento", dia * dias_mes)
    anos = _anos_completos(adm, desl)
    dias_av = calculo.dias_aviso(anos)
    projecao = desl
    aviso_ind = ZERO
    if r.motivo == "sem_justa_causa" and r.aviso == "indenizado":
        aviso_ind = add(f"Aviso prévio indenizado ({dias_av} dias)", "provento", dia * dias_av)
        projecao = desl + datetime.timedelta(days=dias_av)
    elif r.motivo == "acordo" and r.aviso == "indenizado":
        aviso_ind = add(f"Aviso prévio indenizado — 50% ({dias_av} dias)", "provento", dia * dias_av / 2)
        projecao = desl + datetime.timedelta(days=dias_av // 2)
    elif r.motivo == "pedido" and r.aviso == "nao_cumprido":
        add("Desconto de aviso prévio não cumprido", "desconto", sal)
    # 13º proporcional
    dec13 = ZERO
    if r.motivo != "justa_causa":
        avos = _avos_13(colab, projecao.year, projecao)
        dec13 = add(f"13º salário proporcional ({avos}/12)", "provento", sal / 12 * avos)
    # férias vencidas (períodos completos com saldo) e proporcionais (período em curso, com a projeção do aviso)
    from .ferias import gerar_periodos

    gerar_periodos(colab, ate=desl)
    vencidas_total = ZERO
    for per in PeriodoAquisitivo.objects.filter(colaborador=colab, fim__lt=desl).order_by("inicio"):
        if per.saldo > 0:
            v = calculo.ferias(sal, per.saldo)
            vencidas_total += add(f"Férias vencidas ({per.inicio:%d/%m/%Y}–{per.fim:%d/%m/%Y}, {per.saldo} dias)",
                                  "provento", v["ferias"])
            vencidas_total += add("1/3 sobre férias vencidas", "provento", v["terco"])
    if r.motivo != "justa_causa":
        em_curso = PeriodoAquisitivo.objects.filter(colaborador=colab, inicio__lte=desl, fim__gte=desl).first()
        if em_curso:
            avos_f = min(calculo.meses_avos(em_curso.inicio, max(projecao, desl)), 12)
            if avos_f:
                v = calculo.ferias(sal, 30)
                add(f"Férias proporcionais ({avos_f}/12)", "provento", v["ferias"] * avos_f / 12)
                add("1/3 sobre férias proporcionais", "provento", v["terco"] * avos_f / 12)
    # descontos legais
    inss_saldo = calculo.inss(saldo, faixas_inss)
    inss_13 = calculo.inss(dec13, faixas_inss)
    base_ir = saldo + vencidas_total
    ir_saldo, _ = calculo.irrf(base_ir, inss_saldo, sum(1 for d in colab.dependentes.all() if d.deduz_irrf), p, faixas_irrf)
    ir_13, _ = calculo.irrf(dec13, inss_13, 0, p, faixas_irrf, aplicar_reducao=False) if dec13 else (ZERO, ZERO)
    add("INSS sobre saldo de salário", "desconto", inss_saldo)
    add("INSS sobre 13º", "desconto", inss_13)
    add("IRRF sobre saldo de salário", "desconto", ir_saldo)
    add("IRRF sobre 13º", "desconto", ir_13)
    # multa do FGTS
    multa = ZERO
    aliq_multa = {"sem_justa_causa": D("0.40"), "acordo": D("0.20")}.get(r.motivo)
    if aliq_multa:
        depositos = q2((saldo + dec13 + aviso_ind) * p.fgts_aliquota / 100)
        base_multa = r.saldo_fgts + depositos
        multa = q2(base_multa * aliq_multa)
        linhas.append({"descricao": f"Multa do FGTS ({int(aliq_multa * 100)}% sobre {base_multa:.2f}) — paga ao FGTS",
                       "tipo": "informativo", "valor": str(multa)})
    proventos = sum(D(x["valor"]) for x in linhas if x["tipo"] == "provento")
    descontos = sum(D(x["valor"]) for x in linhas if x["tipo"] == "desconto")
    r.memoria, r.multa_fgts = linhas, multa
    r.total_proventos, r.total_descontos, r.total_liquido = q2(proventos), q2(descontos), q2(proventos - descontos)
    r.status = "calculada"
    r.save()
    return r


@transaction.atomic
def efetivar_rescisao(r):
    from apps.financeiro.models import ContaPagar

    if r.status == "efetivada":
        raise ValidationError("Esta rescisão já foi efetivada.")
    if r.total_liquido <= 0:
        raise ValidationError("Calcule a rescisão antes de efetivar.")
    colab = r.colaborador
    colab.data_desligamento, colab.status = r.data_desligamento, "desligado"
    colab.save()
    t = ContaPagar.objects.create(
        empresa=r.empresa, descricao=f"Rescisão — {colab.nome}", documento=f"RESC {colab.pk}",
        valor=r.total_liquido, vencimento=r.data_desligamento + datetime.timedelta(days=10),
        competencia=r.data_desligamento.replace(day=1), aprovado=True,
        observacoes="Prazo de pagamento das verbas rescisórias: 10 dias (CLT art. 477).")
    r.titulo_id, r.status = t.pk, "efetivada"
    r.save()
    return t
