"""Férias: períodos aquisitivos, regras de concessão e aprovação."""
import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.models import notificar

from . import calculo
from .models import ApuracaoDia, PeriodoAquisitivo


def _somar_anos(data, anos):
    try:
        return data.replace(year=data.year + anos)
    except ValueError:  # 29/02
        return data.replace(year=data.year + anos, day=28)


def gerar_periodos(colab, ate=None):
    """Cria os períodos aquisitivos (a cada aniversário da admissão) até a data; idempotente. Reduz os dias de
    direito pelas faltas injustificadas apuradas no ponto, nos períodos já encerrados."""
    ate = ate or timezone.localdate()
    novos, k = 0, 0
    while True:
        inicio = _somar_anos(colab.data_admissao, k)
        if inicio > ate or (colab.data_desligamento and inicio > colab.data_desligamento):
            break
        fim = _somar_anos(colab.data_admissao, k + 1) - datetime.timedelta(days=1)
        _, criado = PeriodoAquisitivo.objects.get_or_create(colaborador=colab, inicio=inicio, defaults={"fim": fim})
        novos += criado
        k += 1
    for per in PeriodoAquisitivo.objects.filter(colaborador=colab, fim__lt=timezone.localdate()):
        faltas = ApuracaoDia.objects.filter(colaborador=colab, data__range=(per.inicio, per.fim), falta=True).count()
        direito = calculo.direito_ferias(faltas) if faltas else per.dias_direito
        if faltas and direito != per.dias_direito:
            per.dias_direito = direito
            per.save(update_fields=["dias_direito", "atualizado_em"])
    return novos


def validar(s):
    """Regras da CLT (art. 134 e 143) para uma solicitação."""
    per = s.periodo
    if s.inicio <= per.fim:
        raise ValidationError("As férias só podem começar depois de completado o período aquisitivo.")
    if s.inicio > per.limite_concessivo:
        raise ValidationError(f"O período concessivo terminou em {per.limite_concessivo:%d/%m/%Y}: as férias "
                              "venceram (pague em dobro ou negocie com o colaborador).")
    if s.abono_dias > per.dias_direito // 3:
        raise ValidationError(f"O abono pecuniário é de até {per.dias_direito // 3} dias neste período.")
    outras = per.solicitacoes.filter(status__in=["solicitada", "aprovada", "gozada"]).exclude(pk=s.pk)
    usados = sum(o.dias + o.abono_dias for o in outras)
    if usados + s.dias + s.abono_dias > per.dias_direito:
        raise ValidationError(f"Saldo insuficiente: restam {per.dias_direito - usados} dias neste período.")
    partes = [o.dias for o in outras] + [s.dias]
    if len(partes) > 3:
        raise ValidationError("As férias podem ser divididas em no máximo 3 períodos.")
    restante = per.dias_direito - sum(o.dias + o.abono_dias for o in outras) - s.dias - s.abono_dias
    if len(partes) > 1 and max(partes) < 14 and restante <= 0:
        raise ValidationError("Em férias fracionadas, um dos períodos deve ter 14 dias ou mais.")
    if len(partes) == 1 and restante > 0 and s.dias < 14 and restante < 14 - s.dias:
        raise ValidationError("Um dos períodos de férias precisa ter 14 dias ou mais.")
    cruza = [o for o in outras if o.inicio <= s.fim and s.inicio <= o.fim]
    if cruza:
        raise ValidationError("Este gozo se sobrepõe a outra solicitação do mesmo colaborador.")


def solicitar(s, usuario=None):
    validar(s)
    s.status = "solicitada"
    s.save()
    destinos = [x.usuario for x in (s.colaborador.gestor,) if x and x.usuario]
    notificar(destinos, f"{s.colaborador} solicitou férias de {s.inicio:%d/%m/%Y} ({s.dias} dias).",
              url=s.get_absolute_url())
    return s


def decidir(s, usuario, aprovar, motivo=""):
    if s.status != "solicitada":
        raise ValidationError("Esta solicitação já foi decidida.")
    if aprovar:
        validar(s)
    elif not motivo:
        raise ValidationError("Informe o motivo da recusa.")
    s.status = "aprovada" if aprovar else "recusada"
    s.decidido_por, s.decidido_em, s.motivo_recusa = usuario, timezone.now(), motivo
    s.save()
    if s.colaborador.usuario:
        notificar([s.colaborador.usuario],
                  f"Suas férias de {s.inicio:%d/%m/%Y} foram {'aprovadas' if aprovar else 'recusadas'}.",
                  url=s.get_absolute_url())
    return s


def pode_decidir(usuario, s):
    if usuario.has_perm("rh.aprovar_solicitacaoferias"):
        return True
    gestor = s.colaborador.gestor
    return bool(gestor and gestor.usuario_id == usuario.pk)
