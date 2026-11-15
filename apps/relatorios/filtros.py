"""Filtro comum dos dashboards: empresa/filial e período (com o período anterior para comparação)."""
import calendar
import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.crud import restringir_por_empresa
from apps.core.models import Empresa

PERIODOS = [("mes", "Este mês"), ("mes_ant", "Mês anterior"), ("tri", "Este trimestre"), ("ano", "Este ano"),
            ("12m", "Últimos 12 meses"), ("custom", "Personalizado")]


def _fim_mes(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _somar_meses(d, n):
    m = d.month - 1 + n
    return datetime.date(d.year + m // 12, m % 12 + 1, 1)


def intervalo(codigo, hoje, de=None, ate=None):
    if codigo == "mes":
        return hoje.replace(day=1), hoje
    if codigo == "mes_ant":
        ini = _somar_meses(hoje.replace(day=1), -1)
        return ini, _fim_mes(ini)
    if codigo == "tri":
        return datetime.date(hoje.year, 3 * ((hoje.month - 1) // 3) + 1, 1), hoje
    if codigo == "ano":
        return datetime.date(hoje.year, 1, 1), hoje
    if codigo == "custom" and de and ate and de <= ate:
        return de, ate
    return _somar_meses(hoje.replace(day=1), -11), hoje        # 12m, padrão


@dataclass
class Filtro:
    user: object
    empresa: object            # Empresa escolhida ou None (todas as que o usuário acessa)
    periodo: str
    ini: datetime.date
    fim: datetime.date
    hoje: datetime.date

    @property
    def dias(self):
        return (self.fim - self.ini).days + 1

    @property
    def ant_ini(self):
        return self.ini - datetime.timedelta(days=self.dias)

    @property
    def ant_fim(self):
        return self.ini - datetime.timedelta(days=1)

    @property
    def meses(self):
        """Primeiro dia de cada mês do período (eixo dos gráficos)."""
        d, out = self.ini.replace(day=1), []
        while d <= self.fim:
            out.append(d)
            d = _somar_meses(d, 1)
        return out

    def escopo(self, qs, caminho="empresa"):
        """Recorta por empresa (campo `empresa` ou um caminho até ela, ex.: armazem__empresa).
        Modelos sem esse campo voltam como estão."""
        try:
            qs.model._meta.get_field(caminho.split("__")[0])
        except Exception:
            return qs
        if self.empresa:
            return qs.filter(**{caminho: self.empresa})
        return restringir_por_empresa(qs, qs.model, self.user, caminho)

    def querystring(self):
        q = f"periodo={self.periodo}"
        if self.empresa:
            q += f"&empresa={self.empresa.pk}"
        if self.periodo == "custom":
            q += f"&de={self.ini:%Y-%m-%d}&ate={self.fim:%Y-%m-%d}"
        return q


def _data(txt):
    try:
        return datetime.date.fromisoformat(txt or "")
    except ValueError:
        return None


def do_request(request):
    hoje = timezone.localdate()
    codigo = request.GET.get("periodo")
    if codigo not in dict(PERIODOS):
        codigo = "12m"
    ini, fim = intervalo(codigo, hoje, _data(request.GET.get("de")), _data(request.GET.get("ate")))
    if codigo == "custom" and (ini, fim) == intervalo("12m", hoje):
        codigo = "12m"
    empresa = None
    pk = request.GET.get("empresa", "")
    if pk.isdigit():   # só vale empresa a que o usuário tem acesso; senão volta para "todas"
        empresa = Empresa.acessiveis(request.user).filter(pk=int(pk)).first()
    return Filtro(request.user, empresa, codigo, ini, fim, hoje)


def variacao(atual, anterior):
    """Variação percentual (None quando não há base de comparação)."""
    anterior = Decimal(anterior or 0)
    if not anterior:
        return None
    return float((Decimal(atual or 0) - anterior) / abs(anterior) * 100)


def q_periodo(campo, ini, fim):
    return Q(**{f"{campo}__gte": ini, f"{campo}__lte": fim})
