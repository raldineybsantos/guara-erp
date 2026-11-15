"""Ponto eletrônico: marcações, apuração diária, espelho e banco de horas."""
import datetime

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Sum
from django.utils import timezone

from .models import AbonoPonto, AjusteBancoHoras, ApuracaoDia, Feriado, Marcacao


def bater_ponto(colaborador, ip=None):
    """Registra a marcação de agora para o colaborador (evita duplo clique em menos de 1 minuto)."""
    agora = timezone.now()
    ultima = colaborador.marcacoes.order_by("-momento").first()
    if ultima and (agora - ultima.momento).total_seconds() < 60:
        raise ValidationError("Já existe uma marcação há menos de 1 minuto.")
    return Marcacao.objects.create(colaborador=colaborador, momento=agora, ip=ip, origem="web")


def _jornada(colaborador):
    try:
        return colaborador.dados_trabalhistas.jornada
    except ObjectDoesNotExist:
        return None


def _minutos_trabalhados(marcas, jornada):
    """Soma dos pares entrada/saída; com só um par de mais de 6h, desconta o intervalo da jornada."""
    pares = len(marcas) // 2
    total = sum(int((marcas[2 * i + 1] - marcas[2 * i]).total_seconds() // 60) for i in range(pares))
    if pares == 1 and total > 360:
        total -= jornada.intervalo_min
    return max(total, 0)


def apurar_dia(colaborador, dia, jornada=None, feriados=None, abonos=None):
    jornada = jornada or _jornada(colaborador)
    if jornada is None or dia < colaborador.data_admissao or (
            colaborador.data_desligamento and dia > colaborador.data_desligamento):
        ApuracaoDia.objects.filter(colaborador=colaborador, data=dia).delete()
        return None
    feriados = feriados if feriados is not None else set(Feriado.objects.filter(data=dia).values_list("data", flat=True))
    abonos = abonos if abonos is not None else set(
        AbonoPonto.objects.filter(colaborador=colaborador, data=dia).values_list("data", flat=True))
    um = datetime.timedelta(days=1)
    marcas = sorted(timezone.localtime(m.momento).replace(tzinfo=None) for m in Marcacao.objects.filter(
        colaborador=colaborador, momento__date__range=(dia - um, dia + um)))
    marcas = [m for m in marcas if m.date() == dia]
    esperado = jornada.esperado_min if dia.weekday() in jornada.dias and dia not in feriados else 0
    abonado = dia in abonos
    if abonado:
        esperado = 0
    trabalhado = _minutos_trabalhados(marcas, jornada)
    saldo = trabalhado - esperado
    if abs(saldo) <= jornada.tolerancia_min:
        saldo = 0
    inconsistente = len(marcas) % 2 == 1
    falta = esperado > 0 and not marcas and dia < timezone.localdate()
    obj, _ = ApuracaoDia.objects.update_or_create(
        colaborador=colaborador, data=dia,
        defaults={"esperado_min": esperado, "trabalhado_min": trabalhado, "saldo_min": saldo if not falta else -esperado,
                  "falta": falta, "inconsistente": inconsistente})
    return obj


def apurar_periodo(colaborador, de, ate):
    jornada = _jornada(colaborador)
    if jornada is None:
        return []
    feriados = set(Feriado.objects.filter(data__range=(de, ate)).values_list("data", flat=True))
    abonos = set(AbonoPonto.objects.filter(colaborador=colaborador, data__range=(de, ate)).values_list("data", flat=True))
    res, dia = [], de
    while dia <= ate:
        obj = apurar_dia(colaborador, dia, jornada, feriados, abonos)
        if obj:
            res.append(obj)
        dia += datetime.timedelta(days=1)
    return res


def saldo_banco(colaborador, ate=None):
    """Saldo do banco de horas em minutos (apurações + ajustes)."""
    ap = ApuracaoDia.objects.filter(colaborador=colaborador)
    aj = AjusteBancoHoras.objects.filter(colaborador=colaborador)
    if ate:
        ap, aj = ap.filter(data__lte=ate), aj.filter(data__lte=ate)
    return (ap.aggregate(s=Sum("saldo_min"))["s"] or 0) + (aj.aggregate(s=Sum("minutos"))["s"] or 0)


def hhmm(minutos):
    sinal = "-" if minutos < 0 else ""
    minutos = abs(int(minutos))
    return f"{sinal}{minutos // 60:02d}:{minutos % 60:02d}"


def espelho(colaborador, ano, mes):
    """Linhas do espelho de ponto do mês: marcações do dia + apuração."""
    import calendar

    ultimo = calendar.monthrange(ano, mes)[1]
    de, ate = datetime.date(ano, mes, 1), datetime.date(ano, mes, ultimo)
    apurar_periodo(colaborador, de, ate)
    marcas = {}
    for m in Marcacao.objects.filter(colaborador=colaborador, momento__date__range=(de - datetime.timedelta(days=1),
                                                                                    ate + datetime.timedelta(days=1))):
        local = timezone.localtime(m.momento)
        marcas.setdefault(local.date(), []).append(local.strftime("%H:%M"))
    apur = {a.data: a for a in ApuracaoDia.objects.filter(colaborador=colaborador, data__range=(de, ate))}
    abonos = {a.data: a for a in AbonoPonto.objects.filter(colaborador=colaborador, data__range=(de, ate))}
    feriados = {f.data: f.nome for f in Feriado.objects.filter(data__range=(de, ate))}
    linhas = []
    for n in range(1, ultimo + 1):
        d = datetime.date(ano, mes, n)
        a = apur.get(d)
        linhas.append({"data": d, "marcacoes": sorted(marcas.get(d, [])), "apuracao": a,
                       "abono": abonos.get(d), "feriado": feriados.get(d),
                       "trabalhado": hhmm(a.trabalhado_min) if a else "", "saldo": hhmm(a.saldo_min) if a else ""})
    totais = {"trabalhado": sum(x["apuracao"].trabalhado_min for x in linhas if x["apuracao"]),
              "saldo": sum(x["apuracao"].saldo_min for x in linhas if x["apuracao"]),
              "faltas": sum(1 for x in linhas if x["apuracao"] and x["apuracao"].falta)}
    return linhas, totais
