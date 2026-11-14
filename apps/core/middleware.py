"""Guarda o usuário e a empresa ativa da requisição atual (auditoria e padrão dos lançamentos)."""
import threading

_local = threading.local()


def usuario_atual():
    return getattr(_local, "usuario", None)


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


class AuditoriaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        logado = user is not None and user.is_authenticated
        _local.usuario = user if logado else None
        _local.empresa = _empresa_da_sessao(request, user) if logado else None
        request.empresa = _local.empresa
        try:
            return self.get_response(request)
        finally:
            _local.usuario = None
            _local.empresa = None
