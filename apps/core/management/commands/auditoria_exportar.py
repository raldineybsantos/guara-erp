import csv
import datetime
import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import LogAuditoria


def _data(texto):
    try:
        return datetime.date.fromisoformat(texto)
    except ValueError:
        raise CommandError(f"Data inválida: {texto} (use AAAA-MM-DD).") from None


class Command(BaseCommand):
    help = ("Exporta a trilha de auditoria para CSV e grava o SHA-256 do arquivo ao lado, para arquivar ou entregar "
            "a um auditor. O sistema não apaga a trilha: guarde as exportações conforme a sua política de retenção.")

    def add_arguments(self, parser):
        parser.add_argument("saida", help="arquivo CSV de destino")
        parser.add_argument("--de", help="data inicial (AAAA-MM-DD)")
        parser.add_argument("--ate", help="data final (AAAA-MM-DD)")
        parser.add_argument("--modelo", help="só um modelo, ex.: financeiro.contapagar")

    def handle(self, saida, de=None, ate=None, modelo=None, **opts):
        qs = LogAuditoria.objects.order_by("id")
        if de:
            qs = qs.filter(data__date__gte=_data(de))
        if ate:
            qs = qs.filter(data__date__lte=_data(ate))
        if modelo:
            qs = qs.filter(modelo=modelo)
        destino = Path(saida)
        n = 0
        with destino.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["id", "data_utc", "usuario", "empresa_id", "acao", "modelo", "objeto_id", "descricao", "ip",
                        "requisicao", "caminho", "alteracoes", "hash"])
            for x in qs.iterator():
                n += 1
                w.writerow([x.pk, x.data.astimezone(datetime.timezone.utc).isoformat(), x.usuario_nome,
                            x.empresa_id or "", x.acao, x.modelo, x.objeto_id, _seguro(x.descricao), x.ip or "",
                            x.requisicao, _seguro(x.caminho), _seguro(x.resumo_alteracoes), x.hash])
        soma = hashlib.sha256(destino.read_bytes()).hexdigest()
        destino.with_name(destino.name + ".sha256").write_text(f"{soma}  {destino.name}\n")
        LogAuditoria.objects.create(acao="exportar", modelo="core.logauditoria",
                                    descricao=f"Exportou {n} registro(s) da trilha para arquivo",
                                    alteracoes={"sha256": soma, "de": de or "", "ate": ate or "", "modelo": modelo or ""})
        self.stdout.write(f"{n} registro(s) em {destino} (SHA-256 {soma}).")


def _seguro(texto):
    from apps.core.crud import celula_csv

    return celula_csv(texto)
