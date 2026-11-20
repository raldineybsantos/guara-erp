"""Varredura de todas as rotas do sistema: nenhuma tela de dados pode abrir sem login, e um usuário logado sem
nenhuma permissão só pode abrir as telas pessoais (as que existem para qualquer funcionário)."""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver

# Rotas públicas por desenho: entrada no sistema, retornos de provedores externos (têm autenticação própria) etc.
PUBLICAS = (
    "/login/", "/cobranca/webhook/", "/assistente/telegram/webhook/", "/identidade/", "/scim/v2/",
    "/seguranca/2fa/", "/ged/verificar/", "/senha/", "/saude/",
)


def rotas():
    def andar(res, prefixo=""):
        for p in res.url_patterns:
            if isinstance(p, URLResolver):
                yield from andar(p, prefixo + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield prefixo + str(p.pattern)
    for r in andar(get_resolver()):
        url = "/" + re.sub(r"\(\?P<\w+>[^)]*\)|<[^>]+>", "1", r.replace("^", "").replace("$", ""))
        if url.startswith("/admin/") or url.startswith(("/static/", "/media/")):
            continue
        yield url


class SemLoginTest(TestCase):
    def test_nenhuma_rota_abre_sem_login(self):
        abertas = []
        for url in rotas():
            if url.startswith(PUBLICAS):
                continue
            for metodo in ("get", "post"):
                r = getattr(self.client, metodo)(url)
                destino = r.headers.get("Location", "")
                if r.status_code == 200 or (r.status_code in (301, 302) and "/login" not in destino):
                    abertas.append(f"{metodo.upper()} {url} -> {r.status_code} {destino}")
        self.assertEqual(abertas, [], "Rotas acessíveis sem login:\n" + "\n".join(abertas))


class SemPermissaoTest(TestCase):
    """Usuário comum, sem permissão alguma, não abre telas de cadastro nem de movimentação."""
    PESSOAIS = ("/rh/ponto/", "/rh/ferias/minhas/", "/rh/ferias/solicitar/", "/rh/holerites/meus/",
                "/rh/treinamentos/meus/", "/rh/desempenho/minhas/", "/tarefas/", "/servicedesk/", "/seguranca/",
                "/logout/", "/primeiros-passos/", "/assistente/", "/consulta-", "/notificacoes/", "/perfil/",
                "/senha/", "/relatorios/", "/escolher-empresa/", "/empresa-ativa/", "/busca/", "/avisos/", "/workflow/tarefas/")

    def setUp(self):
        self.u = get_user_model().objects.create_user("semperm", "s@s.com", "Xk9!pqrs7tuv")
        self.client.force_login(self.u)

    def test_telas_de_dados_negam_acesso(self):
        vazando = []
        for url in rotas():
            if url.startswith(PUBLICAS) or url.startswith(self.PESSOAIS) or url == "/":
                continue
            r = self.client.get(url)
            if r.status_code == 200:
                vazando.append(f"GET {url}")
        self.assertEqual(vazando, [], "Telas abertas a usuário sem permissão:\n" + "\n".join(vazando))
