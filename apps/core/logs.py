"""Registro de erros do sistema (LogErro): exceções não tratadas e mensagens de erro do log.

Erros iguais (mesmo tipo, mesmo ponto do código, mesma tela) são agrupados: a mesma linha conta as ocorrências.
Nada aqui pode piorar a situação: qualquer falha ao gravar o erro é engolida e vai só para o console.
"""

import hashlib
import logging
import re
import sys
import threading
import traceback

from django.conf import settings
from django.db import transaction
from django.utils import timezone

_guarda = threading.local()
SEGREDO = re.compile(r"(?i)(senha|password|passwd|token|secret|api[_-]?key|authorization|cpf|cnpj)\s*[=:]\s*\S+")
MAX_DETALHE = 20000
IGNORADOS = {"django.request", "django.server", "django.security.DisallowedHost", "django.db.backends"}


def _limpar(texto, limite):
    return SEGREDO.sub(lambda m: f"{m.group(1)}=***", texto or "")[:limite]


def _ponto_do_codigo(tb):
    """Último trecho do traceback que está dentro do projeto (onde o erro nasce, não onde o framework o repassa)."""
    base = str(settings.BASE_DIR)
    achado = None
    for fr in traceback.extract_tb(tb) if tb else []:
        if fr.filename.startswith(base) and "site-packages" not in fr.filename:
            achado = fr
    if achado is None and tb:
        achado = traceback.extract_tb(tb)[-1]
    if achado is None:
        return ""
    return f"{achado.filename.replace(base, '')}:{achado.name}:{achado.lineno}"


def _impressao(tipo, ponto, caminho, mensagem):
    chave = f"{tipo}|{ponto}|{re.sub(r'[0-9]+', 'N', caminho or '')}"
    if not ponto:   # sem traceback: agrupa pela mensagem sem números
        chave += "|" + re.sub(r"[0-9]+", "N", mensagem or "")[:120]
    return hashlib.sha1(chave.encode()).hexdigest()


def registrar_erro(exc=None, *, request=None, mensagem="", origem="web", nivel="ERROR", status=None, exc_info=None):
    """Grava (ou soma a) um erro. Devolve o LogErro ou None. Nunca levanta exceção."""
    if getattr(_guarda, "ativo", False):
        return None
    _guarda.ativo = True
    try:
        from .middleware import contexto_requisicao, usuario_atual
        from .models import LogErro

        info = exc_info or ((type(exc), exc, exc.__traceback__) if exc is not None else sys.exc_info())
        tipo_exc, valor, tb = info if info and info[0] else (None, None, None)
        tipo = (tipo_exc.__name__ if tipo_exc else "Erro")[:150]
        texto = _limpar(str(valor) if valor is not None else mensagem, 500) or tipo
        detalhe = _limpar("".join(traceback.format_exception(tipo_exc, valor, tb)) if tipo_exc else mensagem,
                          MAX_DETALHE)
        ctx = contexto_requisicao()
        caminho = request.path if request is not None else (ctx.get("caminho", "").split(" ", 1)[-1].split("?")[0])
        metodo = request.method if request is not None else ctx.get("caminho", "").split(" ", 1)[0][:8]
        if metodo and metodo not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            metodo = ""
        usuario = usuario_atual() or (getattr(request, "user", None) if request is not None else None)
        usuario = usuario if usuario is not None and getattr(usuario, "is_authenticated", False) else None
        ponto = _ponto_do_codigo(tb)
        imp = _impressao(tipo, ponto, caminho, texto)
        agora = timezone.now()
        with transaction.atomic():
            aberto = LogErro.objects.filter(impressao=imp, situacao__in=("novo", "investigando")).first()
            dados = dict(ultima_em=agora, usuario=usuario, usuario_nome=usuario.get_username() if usuario else "",
                         ip=ctx.get("ip"), requisicao=ctx.get("id", ""), status_http=status,
                         empresa_id=ctx.get("empresa_id"))
            if aberto:
                LogErro.objects.filter(pk=aberto.pk).update(ocorrencias=models_f("ocorrencias") + 1, **dados)
                aberto.refresh_from_db()
                return aberto
            reincidente = LogErro.objects.filter(impressao=imp, situacao="resolvido").exists()
            novo = LogErro.objects.create(impressao=imp, tipo=tipo, mensagem=texto, detalhe=detalhe, origem=origem,
                                          nivel=nivel, metodo=metodo or "", caminho=(caminho or "")[:300],
                                          reincidente=reincidente, **dados)
        _avisar_administradores(novo)
        return novo
    except Exception as e:   # nunca derrubar quem já está tratando um erro
        print(f"[logs] não foi possível gravar o erro: {e.__class__.__name__}: {e}", file=sys.stderr)
        return None
    finally:
        _guarda.ativo = False


def models_f(campo):
    from django.db.models import F

    return F(campo)


def _avisar_administradores(erro):
    """Aviso no sino para quem administra, só na primeira vez de cada erro (as repetições só somam)."""
    try:
        from django.contrib.auth import get_user_model

        from .models import notificar

        from django.urls import reverse

        admins = get_user_model().objects.filter(is_active=True, is_superuser=True)
        notificar(admins, f"Erro no sistema: {erro.tipo} em {erro.caminho or erro.origem}"[:200],
                  url=reverse("core:logerro_detail", args=[erro.pk]))
    except Exception:
        pass


class LogErroHandler(logging.Handler):
    """Leva para o banco tudo que o código registra como ERROR/CRITICAL (exceto o que o middleware já captura)."""

    def emit(self, record):
        if record.levelno < logging.ERROR or record.name in IGNORADOS or record.name.startswith(tuple(IGNORADOS)):
            return
        registrar_erro(mensagem=record.getMessage(), origem="log", nivel=record.levelname,
                       exc_info=record.exc_info if record.exc_info and record.exc_info[0] else None)
