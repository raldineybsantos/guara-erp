"""Auditoria automática: registra criação, alteração e exclusão nos módulos do ERP."""
import hashlib
from decimal import Decimal

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

# Auditado: tudo o que é do ERP (apps.*) e as contas de acesso (usuários e grupos), menos o que é ruído ou é a
# própria trilha. Segredos nunca entram (ver IGNORAR); a senha aparece só como impressão digital, para provar
# que mudou sem revelar nada.
EXTRAS = {"auth.user", "auth.group"}
EXCLUIR = {"core.logauditoria", "core.seloauditoria", "core.logerro", "core.notificacao",
           # derivados e recalculáveis; as entradas (marcações, lançamentos, folha) continuam auditadas
           "rh.apuracaodia", "rh.holerite", "rh.itemholerite",
           # segredos e códigos: os eventos de 2FA vão para a trilha como "auth.mfa" (sem valores)
           "seguranca.dispositivomfa", "seguranca.chaveseguranca", "seguranca.codigorecuperacao", "seguranca.registrobackup"}
# identidades externas mudam a cada login: o evento de login já fica em auth.login
EXCLUIR |= {"identidade.identidadeexterna", "cobranca.eventogateway", "cobranca.clientegateway"}
IGNORAR = {"criado_em", "atualizado_em", "certificado", "senha", "chave_api", "telegram_token", "telegram_segredo",
           "bind_senha", "client_secret", "chave_api", "token_webhook", "pix_qrcode_png", "xml_enviado", "xml_retorno", "xml", "xml_assinado", "last_login", "detalhe"}
MASCARAR = {"password"}
_cache_apps = {}


def _app_do_erp(label):
    if label not in _cache_apps:
        from django.apps import apps

        try:
            _cache_apps[label] = apps.get_app_config(label).name.startswith("apps.")
        except LookupError:
            _cache_apps[label] = False
    return _cache_apps[label]


def _auditado(sender):
    meta = sender._meta
    rotulo = meta.label_lower
    if rotulo in EXCLUIR or meta.auto_created:
        return False
    return rotulo in EXTRAS or _app_do_erp(meta.app_label)


def _valores(obj):
    dados = {}
    for f in obj._meta.concrete_fields:
        if f.name in IGNORAR:
            continue
        v = getattr(obj, f.attname)
        if f.name in MASCARAR:
            v = "***" + hashlib.sha256(str(v).encode()).hexdigest()[:8] if v else ""
        elif isinstance(v, Decimal):
            v = f"{v:.{f.decimal_places}f}"
        elif hasattr(v, "isoformat"):
            v = v.isoformat()
        elif hasattr(v, "name") and not isinstance(v, (str, int)):  # FieldFile
            v = v.name
        dados[f.name] = v
    return dados


@receiver(pre_save)
def guardar_estado_anterior(sender, instance, **kwargs):
    if not _auditado(sender) or not instance.pk:
        return
    antigo = sender.objects.filter(pk=instance.pk).first()
    instance._estado_anterior = _valores(antigo) if antigo else None


@receiver(post_save)
def registrar_salvamento(sender, instance, created, raw=False, **kwargs):
    if raw or not _auditado(sender):
        return
    from .models import LogAuditoria

    if created:
        LogAuditoria.registrar("criar", instance)
        return
    anterior = getattr(instance, "_estado_anterior", None) or {}
    atual = _valores(instance)
    diff = {k: [anterior.get(k), v] for k, v in atual.items() if anterior.get(k) != v}
    if diff:
        LogAuditoria.registrar("alterar", instance, alteracoes=diff)


@receiver(post_delete)
def registrar_exclusao(sender, instance, **kwargs):
    if not _auditado(sender):
        return
    from .models import LogAuditoria

    LogAuditoria.registrar("excluir", instance)


# --------------------------------------------------------------------------- login
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed  # noqa: E402


def ip_da_requisicao(request):
    """IP do cliente. Só confia em X-Forwarded-For se o ERP estiver atrás de um proxy configurado."""
    from django.conf import settings

    if request is None:
        return None
    if getattr(settings, "CONFIAR_X_FORWARDED_FOR", False):
        fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _log_login(acao, descricao, usuario=None, request=None, username=""):
    from .models import LogAuditoria

    LogAuditoria.objects.create(usuario=usuario, acao="acao", modelo="auth.login", objeto_id=username[:50],
                                descricao=descricao[:300], ip=ip_da_requisicao(request))


@receiver(user_logged_in)
def registrar_login(sender, request, user, **kwargs):
    _log_login("acao", f"Login de {user.username}", user, request, user.username)


@receiver(user_logged_out)
def registrar_logout(sender, request, user, **kwargs):
    if user:
        _log_login("acao", f"Logout de {user.username}", user, request, user.username)


@receiver(user_login_failed)
def registrar_falha(sender, credentials, request=None, **kwargs):
    username = (credentials or {}).get("username", "") or ""
    _log_login("acao", f"FALHA de login: {username}", None, request, username)


# --------------------------------------------------------------------------- acessos e permissões
from django.db.models.signals import m2m_changed  # noqa: E402


def _registrar_m2m(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    rotulo = instance._meta.label_lower
    campo = sender._meta.model_name
    if not (rotulo in EXTRAS or _app_do_erp(instance._meta.app_label)):
        return
    from .models import LogAuditoria

    nomes = sorted(str(o) for o in model.objects.filter(pk__in=pk_set or [])) if pk_set else []
    if action == "post_clear":
        verbo, nomes = "removeu todos", []
    else:
        verbo = "adicionou" if action == "post_add" else "removeu"
    LogAuditoria.objects.create(
        usuario=None, acao="seguranca", modelo=rotulo, objeto_id=str(instance.pk),
        descricao=f"{verbo.capitalize()} em {campo}: {', '.join(nomes)[:200] if nomes else '(todos)'} — {instance}"[:300],
        alteracoes={campo: {"acao": action.replace("post_", ""), "itens": nomes}})


m2m_changed.connect(_registrar_m2m, dispatch_uid="auditoria_m2m")
