"""Guarda o usuário da requisição atual para auditoria automática."""
import threading

_local = threading.local()


def usuario_atual():
    return getattr(_local, "usuario", None)


class AuditoriaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        _local.usuario = user if user is not None and user.is_authenticated else None
        try:
            return self.get_response(request)
        finally:
            _local.usuario = None
