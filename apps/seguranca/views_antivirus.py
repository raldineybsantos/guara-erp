# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.models import LogAuditoria

from . import antivirus, varredura


def _admin(u):
    return u.is_superuser


@login_required
@user_passes_test(_admin)
def painel(request):
    from apps.ged.models import Versao

    no_ar, versao, erro = False, "", ""
    if antivirus.ativo():
        try:
            no_ar = antivirus.ping()
            versao = antivirus.versao() if no_ar else ""
        except antivirus.AntivirusErro as exc:
            erro = str(exc)
    contagem = dict(Versao.objects.values_list("av_situacao").annotate(n=Count("pk")))
    return render(request, "seguranca/antivirus.html", {
        "titulo": "Antivírus (ClamAV)", "icone": "bug", "ativo": antivirus.ativo(), "no_ar": no_ar, "versao": versao, "erro": erro,
        "destino": settings.CLAMAV_SOCKET or f"{settings.CLAMAV_HOST}:{settings.CLAMAV_PORT}",
        "limite": settings.ANTIVIRUS_LIMITE_MB, "limite_envio": settings.ARQUIVO_MAX_MB, "contagem": contagem,
        "pendentes": contagem.get("nao_verificado", 0) + contagem.get("erro", 0),
        "ocorrencias": LogAuditoria.objects.filter(modelo="seguranca.antivirus").order_by("-data")[:30]})


@login_required
@user_passes_test(_admin)
@require_POST
def testar(request):
    """Envia o arquivo de teste EICAR: se o ClamAV detectar, a detecção está funcionando de ponta a ponta."""
    try:
        r = antivirus.escanear(iter([antivirus.eicar()]))
    except antivirus.AntivirusErro as exc:
        messages.error(request, f"O ClamAV não respondeu corretamente: {exc}")
    else:
        if r.situacao == "infectado":
            messages.success(request, f"Teste OK: o arquivo de teste EICAR foi detectado ({r.assinatura}).")
        else:
            messages.error(request, "O ClamAV respondeu, mas NÃO detectou o arquivo de teste EICAR. Confira as assinaturas.")
    return redirect("seguranca:antivirus")


@login_required
@user_passes_test(_admin)
@require_POST
def varrer(request):
    if not antivirus.ping():
        messages.error(request, "O ClamAV não está respondendo.")
    else:
        r = varredura.varrer()
        messages.success(request, f"Varredura concluída: {r['limpo']} sem ameaças, {r['infectado']} com ameaça, "
                                  f"{r['erro'] + r['ausente']} não verificados.")
    return redirect("seguranca:antivirus")
