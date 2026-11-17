# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regras do segundo fator: quem precisa, verificação (TOTP ou código de recuperação), bloqueio e auditoria."""
import datetime
import hashlib
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import totp
from .models import ChaveSeguranca, CodigoRecuperacao, DispositivoMFA

MODELO_LOG = "auth.mfa"
QTD_CODIGOS = 10


def ativo():
    return getattr(settings, "MFA_ATIVO", True)


def exigido(user) -> bool:
    """Superusuários e membros dos grupos em MFA_GRUPOS_OBRIGATORIOS não entram sem o segundo fator."""
    if not ativo() or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    grupos = set(getattr(settings, "MFA_GRUPOS_OBRIGATORIOS", ()))
    return bool(grupos) and user.groups.filter(name__in=grupos).exists()


def dispositivo(user):
    return DispositivoMFA.objects.filter(usuario=user, confirmado=True).first()


def chaves(user):
    return ChaveSeguranca.objects.filter(usuario=user)


def tem_fator(user) -> bool:
    """Tem aplicativo autenticador OU chave de segurança cadastrados."""
    return dispositivo(user) is not None or chaves(user).exists()


def precisa_verificar(user) -> bool:
    return ativo() and user.is_authenticated and (exigido(user) or tem_fator(user))


def iniciar_sessao(request, user):
    """Chamado logo após a senha: a sessão só fica 'liberada' sem 2FA se o usuário não precisa dele."""
    request.session["mfa_ok"] = not precisa_verificar(user)


def liberar(request):
    request.session["mfa_ok"] = True
    request.session.cycle_key()


def registrar(user, descricao, request=None):
    from apps.core.models import LogAuditoria
    from apps.core.signals import ip_da_requisicao

    LogAuditoria.objects.create(usuario=user, acao="seguranca", modelo=MODELO_LOG, objeto_id=user.username[:50],
                                descricao=descricao[:300], ip=ip_da_requisicao(request) if request else None)


def bloqueado(user) -> bool:
    from apps.core.models import LogAuditoria

    desde = timezone.now() - datetime.timedelta(minutes=settings.LOGIN_BLOQUEIO_MINUTOS)
    falhas = LogAuditoria.objects.filter(modelo=MODELO_LOG, objeto_id=user.username[:50],
                                         descricao__startswith="FALHA", data__gte=desde).count()
    return falhas >= settings.LOGIN_MAX_TENTATIVAS


def _impressao(codigo: str) -> str:
    limpo = "".join(c for c in codigo.lower() if c.isalnum())
    return hashlib.sha256(limpo.encode()).hexdigest()


def gerar_codigos_recuperacao(user):
    """Substitui os códigos anteriores. Devolve os novos em texto — única vez em que podem ser vistos."""
    with transaction.atomic():
        CodigoRecuperacao.objects.filter(usuario=user).delete()
        novos = []
        for _ in range(QTD_CODIGOS):
            c = secrets.token_hex(5)
            novos.append(f"{c[:5]}-{c[5:]}")
            CodigoRecuperacao.objects.create(usuario=user, impressao=_impressao(c))
    return novos


def codigos_restantes(user) -> int:
    return CodigoRecuperacao.objects.filter(usuario=user, usado_em__isnull=True).count()


def _gastar_recuperacao(user, informado: str) -> bool:
    n = CodigoRecuperacao.objects.filter(usuario=user, impressao=_impressao(informado),
                                         usado_em__isnull=True).update(usado_em=timezone.now())
    return n == 1


def conferir(user, informado: str) -> str | None:
    """Confere TOTP ou código de recuperação. Devolve 'totp', 'recuperacao' ou None. Não olha bloqueio."""
    informado = (informado or "").strip()
    if not informado or not tem_fator(user):
        return None
    with transaction.atomic():
        disp = dispositivo(user)
        if disp is not None:
            disp = DispositivoMFA.objects.select_for_update().get(pk=disp.pk)
            passo = totp.verificar(disp.obter_segredo(), informado, disp.ultimo_passo)
            if passo is not None:
                disp.ultimo_passo = passo
                disp.save(update_fields=["ultimo_passo"])
                return "totp"
        if len("".join(c for c in informado if c.isalnum())) == 10 and _gastar_recuperacao(user, informado):
            return "recuperacao"
    return None


def verificar(request, user, informado: str) -> bool:
    """Fluxo completo de verificação com bloqueio e auditoria."""
    if bloqueado(user):
        registrar(user, "FALHA de 2FA: usuário bloqueado temporariamente por excesso de tentativas", request)
        return False
    como = conferir(user, informado)
    if como is None:
        registrar(user, f"FALHA de 2FA: {user.username}", request)
        return False
    if como == "recuperacao":
        registrar(user, f"2FA verificado com código de recuperação ({codigos_restantes(user)} restante(s))", request)
    else:
        registrar(user, f"2FA verificado: {user.username}", request)
    return True


def iniciar_cadastro(user) -> DispositivoMFA:
    """Cria (ou reaproveita, se ainda não confirmado) o segredo que será mostrado no QR."""
    disp, _ = DispositivoMFA.objects.get_or_create(usuario=user, defaults={})
    if not disp.confirmado and not disp.segredo:
        disp.definir_segredo(totp.novo_segredo())
        disp.save()
    return disp


def confirmar_cadastro(request, user, informado: str):
    """Confirma o aplicativo com um código. Devolve a lista de códigos de recuperação ou None."""
    disp = DispositivoMFA.objects.filter(usuario=user, confirmado=False).first()
    if disp is None or bloqueado(user):
        return None
    passo = totp.verificar(disp.obter_segredo(), informado, 0)
    if passo is None:
        registrar(user, f"FALHA ao ativar 2FA: {user.username}", request)
        return None
    disp.ultimo_passo, disp.confirmado, disp.confirmado_em = passo, True, timezone.now()
    disp.save()
    registrar(user, f"2FA ativado: {user.username}", request)
    return gerar_codigos_recuperacao(user)


def redefinir(user, por, request=None):
    """Remove todos os segundos fatores (celular/chave perdidos). Quem é obrigado a ter 2FA cadastra outro no próximo login."""
    DispositivoMFA.objects.filter(usuario=user).delete()
    ChaveSeguranca.objects.filter(usuario=user).delete()
    CodigoRecuperacao.objects.filter(usuario=user).delete()
    registrar(por, f"2FA de {user.username} redefinido por {por.username}", request)
