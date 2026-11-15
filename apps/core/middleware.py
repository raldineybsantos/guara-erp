"""Guarda o usuário e a empresa ativa da requisição atual (auditoria e padrão dos lançamentos)."""
import threading

_local = threading.local()


def usuario_atual():
    return getattr(_local, "usuario", None)


def contexto_requisicao():
    """Dados da requisição em curso (IP, navegador, método e caminho, código); vazio fora de uma requisição."""
    return getattr(_local, "requisicao", None) or {}


def empresa_atual():
    """Empresa em que o usuário está trabalhando (escolhida na barra superior). None fora de requisição."""
    return getattr(_local, "empresa", None)


def _empresa_da_sessao(request, user):
    from .models import Empresa

    acessiveis = Empresa.acessiveis(user)
    escolhida = acessiveis.filter(pk=request.session.get("empresa_id") or 0).first()
    if escolhida is None:
        colab = getattr(user, "colaborador", None) if hasattr(user, "colaborador") else None
        escolhida = (acessiveis.filter(pk=colab.empresa_id).first() if colab and colab.empresa_id else None) \
            or acessiveis.order_by("-matriz", "pk").first()
    return escolhida


def _ip(request):
    from django.conf import settings

    if getattr(settings, "CONFIAR_X_FORWARDED_FOR", False):
        fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if fwd:
            return fwd.split(",")[0].strip()[:45]
    return request.META.get("REMOTE_ADDR")


def _caminho(request):
    """Método e caminho, sem valores de parâmetros (podem conter dados pessoais ou segredos)."""
    chaves = ",".join(sorted(request.GET.keys()))
    return f"{request.method} {request.path}" + (f"?{chaves}" if chaves else "")[:150]


class AuditoriaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import uuid

        user = getattr(request, "user", None)
        logado = user is not None and user.is_authenticated
        _local.usuario = user if logado else None
        _local.empresa = _empresa_da_sessao(request, user) if logado else None
        request.empresa = _local.empresa
        request.requisicao_id = uuid.uuid4().hex[:12]
        _local.requisicao = {"id": request.requisicao_id, "ip": _ip(request), "caminho": _caminho(request)[:300],
                             "agente": (request.META.get("HTTP_USER_AGENT") or "")[:200],
                             "empresa_id": _local.empresa.pk if _local.empresa else None}
        try:
            resposta = self.get_response(request)
            resposta["X-Request-ID"] = request.requisicao_id
            if resposta.status_code == 403 and logado:
                from .models import LogAuditoria

                try:
                    LogAuditoria.objects.create(usuario=user, acao="negado", modelo="auth.acesso",
                                                descricao=f"Acesso negado: {request.method} {request.path}"[:300])
                except Exception:   # a negativa já foi dada; o registro não pode derrubar a resposta
                    pass
            return resposta
        finally:
            _local.usuario = None
            _local.empresa = None
            _local.requisicao = None

    def process_exception(self, request, exception):
        from django.core.exceptions import PermissionDenied, SuspiciousOperation
        from django.http import Http404

        if isinstance(exception, (Http404, PermissionDenied)):
            return None
        from .logs import registrar_erro

        registrar_erro(exception, request=request,
                       status=400 if isinstance(exception, SuspiciousOperation) else 500)
        return None
