# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Varredura dos arquivos já guardados no GED (os enviados antes do antivírus ou que ficaram sem verificação)."""
from django.utils import timezone

from . import antivirus


def varrer(situacoes=("nao_verificado", "erro"), limite=None, avisar=True):
    """Examina as versões do GED nas situações dadas. Devolve contagens. Não levanta se o ClamAV estiver fora."""
    from apps.core.models import LogAuditoria
    from apps.ged.models import Versao

    cont = {"limpo": 0, "infectado": 0, "erro": 0, "ausente": 0}
    qs = Versao.objects.filter(av_situacao__in=situacoes).order_by("pk")
    if limite:
        qs = qs[:limite]
    for v in qs:
        agora = timezone.now()
        try:
            with v.arquivo.open("rb") as f:
                if v.tamanho > antivirus.limite_bytes():
                    raise antivirus.AntivirusErro(f"arquivo maior que o limite de {antivirus.limite_bytes() // 1048576} MB")
                r = antivirus.escanear(iter(lambda: f.read(antivirus.CHUNK), b""))
        except FileNotFoundError:
            v.av_situacao, v.av_detalhe, v.av_verificado_em = "erro", "arquivo não encontrado no armazenamento", agora
            cont["ausente"] += 1
        except antivirus.AntivirusErro as exc:
            v.av_situacao, v.av_detalhe, v.av_verificado_em = "erro", str(exc)[:200], agora
            cont["erro"] += 1
        else:
            v.av_situacao, v.av_verificado_em = r.situacao, agora
            v.av_detalhe = r.assinatura
            cont[r.situacao] += 1
            if r.situacao == "infectado":
                LogAuditoria.objects.create(usuario=None, acao="seguranca", modelo="seguranca.antivirus", objeto_id=str(v.pk),
                                            descricao=f"Varredura: ameaça {r.assinatura} em {v}"[:300])
        v.save(update_fields=["av_situacao", "av_verificado_em", "av_detalhe"])
    if avisar and cont["infectado"]:
        from django.contrib.auth.models import User

        from apps.core.models import notificar

        notificar(User.objects.filter(is_superuser=True, is_active=True),
                  f"Antivírus: {cont['infectado']} arquivo(s) do GED com ameaça detectada. Os downloads foram bloqueados.",
                  "/seguranca/antivirus/", email=True)
    return cont


def rotina_diaria():
    """Chamada pelas rotinas diárias: avisa se o ClamAV está fora e verifica o que ficou pendente (até 200 por dia)."""
    if not antivirus.ativo():
        return {"ativo": False}
    no_ar = antivirus.ping()
    if not no_ar:
        antivirus._avisar_indisponivel("O ClamAV não respondeu na verificação diária.")
        return {"ativo": True, "no_ar": False}
    return {"ativo": True, "no_ar": True, **varrer(limite=200)}
