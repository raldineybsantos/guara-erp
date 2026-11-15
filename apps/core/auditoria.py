"""Integridade da trilha de auditoria.

Cada registro leva o seu hash (SHA-256 do conteúdo). Periodicamente os registros são **selados**: um selo cobre
uma faixa de registros e se encadeia ao selo anterior, de modo que alterar, apagar ou inserir um registro no meio
do histórico quebra a cadeia daquele ponto em diante. Além disso o banco recusa UPDATE e DELETE na tabela
(gatilho criado pela migração). O selo não usa trava global: gravar auditoria nunca espera outra transação.
"""

import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

MARGEM_SELO = timedelta(minutes=5)   # só sela o que já assentou (evita transação lenta com id baixo)
CAMPOS_HASH = ("data", "usuario_nome", "acao", "modelo", "objeto_id", "descricao", "alteracoes", "ip", "caminho",
               "requisicao")


def _canonico(valor):
    return json.dumps(valor, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def hash_registro(dados: dict) -> str:
    """SHA-256 do conteúdo de um registro. `data` entra em UTC com microssegundos."""
    d = dict(dados)
    data = d.get("data")
    if data is not None and hasattr(data, "astimezone"):
        from datetime import timezone as tz

        d["data"] = data.astimezone(tz.utc).isoformat()
    return hashlib.sha256(_canonico([d.get(c) if c != "alteracoes" else (d.get(c) or {}) for c in CAMPOS_HASH])
                          .encode("utf-8")).hexdigest()


def hash_do_log(log) -> str:
    return hash_registro({c: getattr(log, c) for c in CAMPOS_HASH})


def hash_selo(anterior: str, hashes) -> str:
    h = hashlib.sha256((anterior or "").encode())
    for x in hashes:
        h.update(x.encode())
    return h.hexdigest()


@transaction.atomic
def selar(agora=None, forcar=False):
    """Sela os registros ainda não selados mais antigos que a margem. Devolve o selo criado ou None."""
    from .models import LogAuditoria, SeloAuditoria

    agora = agora or timezone.now()
    corte = agora if forcar else agora - MARGEM_SELO
    ultimo = SeloAuditoria.objects.order_by("-ate_id").first()
    base = LogAuditoria.objects.filter(id__gt=ultimo.ate_id if ultimo else 0, data__lte=corte).order_by("id")
    linhas = list(base.values_list("id", "hash"))
    if not linhas:
        return None
    return SeloAuditoria.objects.create(
        de_id=linhas[0][0], ate_id=linhas[-1][0], quantidade=len(linhas), anterior=ultimo.hash if ultimo else "",
        hash=hash_selo(ultimo.hash if ultimo else "", [h for _, h in linhas]))


def verificar(selar_antes=True):
    """Confere a trilha. Devolve um dicionário com o resultado e as inconsistências encontradas."""
    from .models import LogAuditoria, SeloAuditoria

    if selar_antes:
        selar()
    problemas, total, sem_hash = [], 0, 0
    por_id = {}
    for pk, h, *resto in LogAuditoria.objects.order_by("id").values_list("id", "hash", *CAMPOS_HASH).iterator():
        total += 1
        if not h:
            sem_hash += 1
            continue
        dados = dict(zip(CAMPOS_HASH, resto))
        por_id[pk] = h
        if hash_registro(dados) != h:
            problemas.append(f"Registro {pk}: o conteúdo não confere com o hash (foi alterado).")
    anterior, selos = "", 0
    for s in SeloAuditoria.objects.order_by("ate_id"):
        selos += 1
        if s.anterior != anterior:
            problemas.append(f"Selo {s.pk}: não se encadeia ao selo anterior (selo removido ou trocado).")
        hashes = [por_id[i] for i in sorted(por_id) if s.de_id <= i <= s.ate_id]
        sem_selo_hash = LogAuditoria.objects.filter(id__gte=s.de_id, id__lte=s.ate_id, hash="").exists()
        if len(hashes) != s.quantidade and not sem_selo_hash:
            problemas.append(f"Selo {s.pk}: cobria {s.quantidade} registro(s), hoje há {len(hashes)} "
                             "(registro apagado ou inserido).")
        elif hash_selo(s.anterior, hashes) != s.hash and not sem_selo_hash:
            problemas.append(f"Selo {s.pk}: o encadeamento dos registros {s.de_id} a {s.ate_id} não confere.")
        anterior = s.hash
    return {"ok": not problemas, "registros": total, "selos": selos, "sem_hash": sem_hash,
            "problemas": problemas}


def manutencao(hoje=None):
    """Rotina diária: sela a trilha e apaga erros já tratados com mais de LOG_ERRO_RETENCAO_DIAS dias.
    A trilha de auditoria em si nunca é apagada pelo sistema."""
    from django.conf import settings

    from .models import LogErro

    selo = selar()
    corte = (hoje or timezone.now()) - timedelta(days=settings.LOG_ERRO_RETENCAO_DIAS)
    apagados, _ = LogErro.objects.filter(situacao__in=("resolvido", "ignorado"), ultima_em__lt=corte).delete()
    return {"selo": selo, "erros_apagados": apagados}
