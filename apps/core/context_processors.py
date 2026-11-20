"""Menu lateral montado a partir das telas registradas e das permissões do usuário."""
from django.conf import settings
from django.urls import reverse

from .crud import REGISTRO

ORDEM_GRUPOS = ["Principal", "Relatórios", "Service Desk", "Cadastros", "Compras", "Vendas", "CRM", "Estoque", "Produção", "Qualidade", "Manutenção", "Projetos", "RH", "SST / EHS", "Financeiro", "Contabilidade", "Cobrança", "Fiscal", "Jurídico", "Workflow / BPM", "GED", "Administração"]
ICONES_GRUPOS = {"Relatórios": "bar-chart-line", "Contabilidade": "journal-text", "Service Desk": "headset", "SST / EHS": "cone-striped", "CRM": "funnel", "Projetos": "kanban", "Manutenção": "wrench-adjustable", "Qualidade": "patch-check", "Produção": "gear-wide-connected", "Vendas": "bag-check", "RH": "people", "Principal": "speedometer2", "Cadastros": "person-lines-fill", "Compras": "cart3", "Estoque": "boxes", "Financeiro": "currency-dollar",
                 "Cobrança": "receipt-cutoff", "Fiscal": "file-earmark-ruled", "Workflow / BPM": "diagram-3",
                 "GED": "archive", "Jurídico": "bank2", "Administração": "gear"}

# (grupo, rótulo, url_name, ícone, permissão, ordem)
LINKS_EXTRAS = [
    ("Principal", "Painel", "dashboard", "speedometer2", None, 1),
    ("Principal", "Assistente de IA", "assistente:conversa", "stars", "assistente.add_solicitacaocadastro", 3),
    ("Administração", "Configuração da IA", "assistente:configuracao", "sliders", "assistente.change_configuracaoia", 3),
    ("RH", "Bater ponto", "rh:bater_ponto", "fingerprint", None, 0),
    ("RH", "Meu espelho de ponto", "rh:meu_espelho", "calendar3", None, 1),
    ("RH", "Minhas férias", "rh:minhas_ferias", "umbrella", None, 2),
    ("RH", "Meus holerites", "rh:meus_holerites", "receipt", None, 3),
    ("RH", "Meus treinamentos", "rh:meus_treinamentos", "mortarboard", None, 4),
    ("RH", "Minhas avaliações", "rh:minhas_avaliacoes", "clipboard2-check", None, 5),
    ("RH", "Posição na faixa salarial", "rh:posicao_faixa", "bar-chart-line", "rh.view_faixasalarial", 103),
    ("RH", "Reajuste coletivo", "rh:reajuste_coletivo", "people", "rh.gerir_salarios", 104),
    ("RH", "Aprovar lote de reajuste", "rh:aprovar_lote", "check2-all", "rh.aprovar_salarios", 105),
    ("RH", "Pendências de treinamento", "rh:pendencias_treinamento", "patch-exclamation", "rh.view_turma", 73),
    ("RH", "Banco de horas", "rh:banco_horas", "hourglass-split", "rh.gerir_ponto", 24),
    ("RH", "Gerar evento eSocial", "rh:esocial_gerar", "cloud-plus", "rh.add_eventoesocial", 58),
    ("RH", "Montar lote do eSocial", "rh:esocial_montar_lote", "box-arrow-down", "rh.view_eventoesocial", 61),
    ("Principal", "Minhas tarefas", "tarefas:quadro", "kanban", None, 2),
    ("Relatórios", "Visão executiva", "relatorios:executivo", "speedometer", None, 0),
    ("Relatórios", "Financeiro", "relatorios:financeiro", "cash-stack",
     ("financeiro.view_contapagar", "financeiro.view_contareceber"), 1),
    ("Relatórios", "Comercial", "relatorios:comercial", "graph-up-arrow",
     ("vendas.view_pedidovenda", "crm.view_oportunidade"), 2),
    ("Relatórios", "Suprimentos", "relatorios:suprimentos", "truck",
     ("compras.view_pedidocompra", "estoque.view_saldo"), 3),
    ("Relatórios", "Operação", "relatorios:operacao", "gear-wide-connected",
     ("producao.view_ordemproducao", "manutencao.view_ordemservico", "qualidade.view_naoconformidade",
      "sst.ver_todos_incidente"), 4),
    ("Relatórios", "Service Desk", "relatorios:servicedesk", "headset", "servicedesk.change_chamado", 5),
    ("Financeiro", "Fluxo de caixa", "financeiro:fluxo_caixa", "graph-up", "financeiro.view_movimentacao", 4),
    ("Financeiro", "Resultado por centro de custo", "financeiro:relatorio_centro_custo", "bullseye", "financeiro.view_centrocusto", 6),
    ("Estoque", "Lotes a vencer", "estoque:lotes_a_vencer", "calendar-x", "estoque.view_saldolote", 8),
    ("Estoque", "Mudar endereço", "estoque:enderecar", "geo-alt", "estoque.add_movimento", 9),
    ("Cobrança", "Importar retorno de cobrança", "bancario:importar_retorno_cobranca", "upload", "bancario.change_remessacobranca", 10),
    ("Financeiro", "Pagar em lote", "financeiro:liquidar_lote_pagar", "cash-stack", "financeiro.liquidar_lote", 11),
    ("Financeiro", "Receber em lote", "financeiro:liquidar_lote_receber", "cash-stack", "financeiro.liquidar_lote", 12),
    ("Financeiro", "Recolher guias em lote", "financeiro:bordero_imposto", "receipt", "financeiro.recolher_bordero_imposto", 13),
    ("Financeiro", "Aging de títulos", "financeiro:aging", "hourglass-split", "financeiro.view_contareceber", 7),
    ("Financeiro", "Apurar PDD", "financeiro:pdd_apurar", "calculator", "financeiro.add_apuracaopdd", 8),
    ("Financeiro", "Transferência", "financeiro:transferencia", "arrow-left-right", "financeiro.add_movimentacao", 5),
    ("Cobrança", "Executar régua agora", "cobranca:executar_regua", "send", "cobranca.change_cobranca", 9),
    ("Jurídico", "Contencioso", "juridico:contencioso", "bank", "juridico.view_processo", 1),
    ("Jurídico", "Painel jurídico", "juridico:painel", "speedometer2", "juridico.view_contrato", 0),
    ("Estoque", "Movimentação manual", "estoque:movimentar", "pencil-square", "estoque.add_movimento", 3),
    ("Estoque", "Reposição (abaixo do mínimo)", "estoque:reposicao", "bell", "estoque.view_saldo", 6),
    ("Service Desk", "Abrir chamado", "servicedesk:catalogo", "plus-circle", "servicedesk.add_chamado", 1),
    ("Service Desk", "Assistente", "servicedesk:assistente", "robot", "servicedesk.add_chamado", 2),
    ("Service Desk", "Fila de atendimento", "servicedesk:fila", "inboxes", "servicedesk.change_chamado", 0),
    ("Administração", "Avisos", "avisos", "bell", None, 0),
    ("Contabilidade", "Novo lançamento", "contabilidade:lancar_manual", "journal-plus", "contabilidade.add_lancamento", 1),
    ("Contabilidade", "Depreciar mês", "contabilidade:depreciar", "calendar-check", "contabilidade.add_depreciacao", 2),
    ("Contabilidade", "Balancete", "contabilidade:balancete", "table", "contabilidade.view_lancamento", 3),
    ("Contabilidade", "Razão", "contabilidade:razao", "journal-bookmark", "contabilidade.view_lancamento", 4),
    ("Contabilidade", "DRE", "contabilidade:dre", "graph-up-arrow", "contabilidade.view_lancamento", 5),
    ("Contabilidade", "Balanço patrimonial", "contabilidade:balanco", "bank", "contabilidade.view_lancamento", 6),
    ("Contabilidade", "Fechamento de períodos", "contabilidade:periodos", "lock", "contabilidade.add_periodofechado", 7),
    ("Contabilidade", "Encerrar exercício", "contabilidade:encerrar_exercicio", "calendar2-check", "contabilidade.add_lancamento", 8),
    ("Contabilidade", "SPED Contábil (ECD)", "contabilidade:gerar_ecd", "file-earmark-text", "contabilidade.view_lancamento", 9),
    ("Financeiro", "Importar extrato (OFX)", "bancario:importar_extrato", "upload", "bancario.add_extrato", 6),
    ("Financeiro", "Importar retorno do banco", "bancario:importar_retorno", "upload", "bancario.change_bordero", 9),
    ("CRM", "Funil de vendas", "crm:funil", "kanban", "crm.view_oportunidade", 1),
    ("Fiscal", "Importar XML NF-e", "fiscal:importar_xml", "upload", "fiscal.add_notafiscal", 5),
    ("Fiscal", "Apuração de impostos", "fiscal:apuracao", "calculator", "fiscal.view_notafiscal", 6),
    ("Workflow / BPM", "Aprovações pendentes", "workflow:minhas_tarefas", "check2-square", None, 0),
    ("Workflow / BPM", "Nova solicitação", "workflow:nova_solicitacao", "plus-circle", "workflow.add_instancia", 1),
    ("Administração", "Integridade da auditoria", "core:auditoria_integridade", "shield-lock",
     "core.view_logauditoria", 91),
    ("Administração", "Usuários e grupos", "admin:auth_user_changelist", "person-gear", "auth.view_user", 1),
    ("Administração", "Banco de dados", "banco_dados", "database", "is_superuser", 2),
    ("Administração", "Login corporativo (LDAP/SSO)", "identidade:painel", "diagram-3", "is_superuser", 5),
    ("Administração", "Gateway de pagamento", "cobranca:gateways", "credit-card-2-front", "is_superuser", 7),
    ("Administração", "Antivírus", "seguranca:antivirus", "bug", "is_superuser", 6),
    ("Administração", "Backups", "seguranca:backups", "cloud-arrow-up", "is_superuser", 4),
    ("Administração", "Dois fatores (2FA)", "seguranca:mfa_usuarios", "shield-check", "is_superuser", 3),
    ("Administração", "Admin completo", "admin:index", "tools", "is_staff", 99),
]


def _tem_perm(user, perm):
    if perm is None:
        return True
    if isinstance(perm, (tuple, list)):   # basta uma das permissões
        return any(_tem_perm(user, p) for p in perm)
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


def avisos(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from .models import Notificacao

    from .models import Empresa

    empresas = list(Empresa.acessiveis(user).order_by("-matriz", "razao_social")[:50])
    return {"avisos_nao_lidos": Notificacao.objects.filter(usuario=user, lida=False).count(),
            "empresa_ativa": getattr(request, "empresa", None), "empresas_acessiveis": empresas}
