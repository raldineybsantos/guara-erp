"""Menu lateral montado a partir das telas registradas e das permissões do usuário."""
from django.conf import settings
from django.urls import reverse

from .crud import REGISTRO

ORDEM_GRUPOS = ["Principal", "Cadastros", "Compras", "Financeiro", "Cobrança", "Fiscal", "Jurídico", "Workflow / BPM", "GED", "Administração"]
ICONES_GRUPOS = {"Principal": "speedometer2", "Cadastros": "person-lines-fill", "Compras": "cart3", "Financeiro": "currency-dollar",
                 "Cobrança": "receipt-cutoff", "Fiscal": "file-earmark-ruled", "Workflow / BPM": "diagram-3",
                 "GED": "archive", "Jurídico": "bank2", "Administração": "gear"}

# (grupo, rótulo, url_name, ícone, permissão, ordem)
LINKS_EXTRAS = [
    ("Principal", "Painel", "dashboard", "speedometer2", None, 1),
    ("Principal", "Minhas tarefas", "tarefas:quadro", "kanban", None, 2),
    ("Financeiro", "Fluxo de caixa", "financeiro:fluxo_caixa", "graph-up", "financeiro.view_movimentacao", 4),
    ("Financeiro", "Transferência", "financeiro:transferencia", "arrow-left-right", "financeiro.add_movimentacao", 5),
    ("Cobrança", "Executar régua agora", "cobranca:executar_regua", "send", "cobranca.change_cobranca", 9),
    ("Jurídico", "Painel jurídico", "juridico:painel", "speedometer2", "juridico.view_contrato", 0),
    ("Fiscal", "Importar XML NF-e", "fiscal:importar_xml", "upload", "fiscal.add_notafiscal", 5),
    ("Fiscal", "Apuração de impostos", "fiscal:apuracao", "calculator", "fiscal.view_notafiscal", 6),
    ("Workflow / BPM", "Aprovações pendentes", "workflow:minhas_tarefas", "check2-square", None, 0),
    ("Workflow / BPM", "Nova solicitação", "workflow:nova_solicitacao", "plus-circle", "workflow.add_instancia", 1),
    ("Administração", "Usuários e grupos", "admin:auth_user_changelist", "person-gear", "auth.view_user", 1),
    ("Administração", "Banco de dados", "banco_dados", "database", "is_superuser", 2),
    ("Administração", "Admin completo", "admin:index", "tools", "is_staff", 99),
]


def _tem_perm(user, perm):
    if perm is None:
        return True
    if perm in ("is_superuser", "is_staff"):
        return getattr(user, perm)
    return user.has_perm(perm)


def menu(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    grupos = {}
    for g, rot, nome, ico, perm, ordem in LINKS_EXTRAS:
        if _tem_perm(user, perm):
            grupos.setdefault(g, []).append({"label": rot, "url": reverse(nome), "icon": ico, "ordem": ordem})
    for c in REGISTRO:
        if c.menu and user.has_perm(c.perm("view")):
            grupos.setdefault(c.menu, []).append({
                "label": c.menu_label or str(c.meta().verbose_name_plural).capitalize(),
                "url": reverse(c.url_name("list")), "icon": c.icon, "ordem": c.menu_order,
            })
    caminho = request.path
    itens = []
    for g in ORDEM_GRUPOS:
        if g in grupos:
            links = sorted(grupos[g], key=lambda x: x["ordem"])
            ativo = False
            for link in links:
                link["ativo"] = link["url"] != "/" and caminho.startswith(link["url"]) or caminho == link["url"]
                ativo = ativo or link["ativo"]
            itens.append({"nome": g, "icone": ICONES_GRUPOS.get(g, "folder"), "links": links, "aberto": ativo})
    return {"menu_erp": itens, "PGADMIN_URL": settings.PGADMIN_URL}
