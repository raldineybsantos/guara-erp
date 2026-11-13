"""Configuração inicial: grupos de acesso, plano de contas, régua de cobrança, processos e pastas padrão.

Pode ser executado várias vezes sem duplicar dados.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

APPS_ERP = ["core", "financeiro", "cobranca", "fiscal", "workflow", "ged", "tarefas", "juridico", "compras"]

BASICO = ["workflow.view_processo", "workflow.view_instancia", "workflow.add_instancia",
          "ged.view_documento", "ged.view_pasta", "ged.add_documento", "ged.change_documento",
          "ged.assinar_documento",
          "tarefas.view_atividade", "tarefas.add_atividade", "tarefas.change_atividade", "tarefas.view_comentario",
          # qualquer pessoa pode pedir uma compra (vê só as próprias solicitações)
          "compras.view_solicitacaocompra", "compras.add_solicitacaocompra", "compras.change_solicitacaocompra",
          "compras.view_itemsolicitacao", "compras.add_itemsolicitacao", "compras.change_itemsolicitacao",
          "compras.delete_itemsolicitacao"]

# Cadastros que não contêm dados pessoais sensíveis. Colaboradores (CPF, salário) e a auditoria
# ficam restritos a RH e Administradores (LGPD / necessidade de saber).
CADASTROS_BASICOS = ["core.view_empresa", "core.view_departamento", "core.view_cargo",
                     "core.view_fornecedor", "core.view_cliente", "core.view_parceironegocio"]

GRUPOS = {
    "Administradores": ["*"] + ["auth.view_user", "auth.add_user", "auth.change_user", "auth.view_group"],
    "Financeiro": ["financeiro.*", "cobranca.*", *CADASTROS_BASICOS, "core.add_fornecedor",
                   "core.change_fornecedor", "core.add_cliente", "core.change_cliente", "core.change_parceironegocio",
                   "fiscal.view_*", "compras.view_pedidocompra", "compras.view_itempedido",
                   "compras.view_recebimentocompra"],
    "Fiscal": ["fiscal.*", *CADASTROS_BASICOS, "core.add_fornecedor", "core.change_fornecedor",
               "core.add_cliente", "core.change_cliente", "core.change_parceironegocio", "financeiro.view_*", "financeiro.add_contapagar",
               "financeiro.add_contareceber"],
    "RH": [*CADASTROS_BASICOS, "core.view_colaborador", "core.add_colaborador", "core.change_colaborador",
           "core.add_departamento", "core.change_departamento", "core.add_cargo", "core.change_cargo",
           "ged.add_pasta"],
    "Jurídico": ["juridico.*", *CADASTROS_BASICOS, "core.add_fornecedor", "core.change_fornecedor",
                 "core.add_cliente", "core.change_cliente", "core.change_parceironegocio", "ged.add_pasta", "financeiro.view_contapagar",
                 "financeiro.view_contareceber"],
    "Compras": ["compras.*", "fiscal.view_produto", "fiscal.add_produto",
                "core.view_fornecedor", "core.add_fornecedor", "core.change_fornecedor", "core.view_parceironegocio",
                "core.change_parceironegocio",
                "financeiro.view_contapagar", "financeiro.add_contapagar", "fiscal.view_*", "fiscal.add_notafiscal"],
    "Usuários": [],
}

PLANO = [
    ("1", "Receitas", "receita", None), ("1.01", "Receita de vendas", "receita", "1"),
    ("1.02", "Receita de serviços", "receita", "1"), ("1.03", "Receitas financeiras", "receita", "1"),
    ("1.09", "Outras receitas", "receita", "1"),
    ("2", "Despesas", "despesa", None), ("2.01", "Pessoal", "despesa", "2"),
    ("2.01.01", "Salários", "despesa", "2.01"), ("2.01.02", "Encargos (INSS/FGTS)", "despesa", "2.01"),
    ("2.01.03", "Benefícios", "despesa", "2.01"), ("2.02", "Administrativas", "despesa", "2"),
    ("2.02.01", "Aluguel", "despesa", "2.02"), ("2.02.02", "Energia, água e internet", "despesa", "2.02"),
    ("2.02.03", "Software e TI", "despesa", "2.02"), ("2.03", "Fornecedores / mercadorias", "despesa", "2"),
    ("2.04", "Impostos e taxas", "despesa", "2"), ("2.05", "Despesas financeiras", "despesa", "2"),
    ("2.09", "Outras despesas", "despesa", "2"),
]

REGUA = [
    ("Lembrete prévio", -3, "Lembrete: {{ titulo.descricao }} vence em {{ vencimento }}"),
    ("No vencimento", 0, "Hoje vence: {{ titulo.descricao }}"),
    ("Atraso 3 dias", 3, "Pagamento em atraso: {{ titulo.descricao }}"),
    ("Atraso 10 dias", 10, "Segundo aviso de atraso: {{ titulo.descricao }}"),
]


def _permissoes(padroes):
    todas = Permission.objects.select_related("content_type")
    resultado = set()
    for p in padroes:
        if p == "*":
            resultado |= set(todas.filter(content_type__app_label__in=APPS_ERP))
            continue
        app, cod = p.split(".", 1)
        qs = todas.filter(content_type__app_label=app)
        if cod == "*":
            resultado |= set(qs)
        elif cod.endswith("*"):
            resultado |= set(qs.filter(codename__startswith=cod[:-1]))
        else:
            resultado |= set(qs.filter(codename=cod))
    return resultado


class Command(BaseCommand):
    help = "Cria grupos, permissões e dados padrão do ERP."

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.cobranca.models import ReguaCobranca
        from apps.financeiro.models import PlanoConta
        from apps.ged.models import Pasta
        from apps.workflow.models import Etapa, Processo

        grupos = {}
        for nome, padroes in GRUPOS.items():
            g, _ = Group.objects.get_or_create(name=nome)
            perms = _permissoes(padroes + BASICO)
            if nome != "Administradores":
                # Alçadas de aprovação: só a administração altera (quem compra não define quem aprova).
                perms = [p for p in perms if not (p.content_type.app_label == "compras"
                                                  and p.codename.endswith("_alcadacompra")
                                                  and not p.codename.startswith("view_"))]
            g.permissions.set(perms)
            grupos[nome] = g
            self.stdout.write(f"Grupo {nome}: {g.permissions.count()} permissões")

        por_codigo = {}
        for codigo, nome, tipo, sup in PLANO:
            pc, _ = PlanoConta.objects.get_or_create(codigo=codigo, defaults={
                "nome": nome, "tipo": tipo, "superior": por_codigo.get(sup)})
            por_codigo[codigo] = pc

        for nome, dias, assunto in REGUA:
            ReguaCobranca.objects.get_or_create(dias=dias, defaults={"nome": nome, "assunto": assunto})

        proc, criado = Processo.objects.get_or_create(codigo="aprovacao-pagamento", defaults={
            "nome": "Aprovação de pagamento",
            "descricao": "Contas a pagar passam pelo financeiro e pela diretoria antes do pagamento."})
        if criado:
            Etapa.objects.create(processo=proc, ordem=1, nome="Conferência do financeiro", tipo="aprovacao",
                                 grupo_responsavel=grupos["Financeiro"], prazo_dias=1,
                                 instrucoes="Confira documento, valor, fornecedor e centro de custo.")
            Etapa.objects.create(processo=proc, ordem=2, nome="Aprovação da diretoria", tipo="aprovacao",
                                 grupo_responsavel=grupos["Administradores"], prazo_dias=2)
        proc2, criado = Processo.objects.get_or_create(codigo="admissao", defaults={
            "nome": "Admissão de colaborador", "descricao": "Fluxo de entrada de novos colaboradores."})
        if criado:
            for i, (nome, grupo) in enumerate([("Coletar documentos", "RH"), ("Criar acessos de TI", "Administradores"),
                                               ("Integração / boas-vindas", "RH")], start=1):
                Etapa.objects.create(processo=proc2, ordem=i, nome=nome, tipo="tarefa", grupo_responsavel=grupos[grupo])

        # Aprovação de pedidos de compra pela alçada (Compras → Alçadas de aprovação): o próprio pedido
        # designa o aprovador de cada etapa e pula os níveis que o valor não exige.
        proc4, criado = Processo.objects.get_or_create(codigo="aprovacao-compra-alcada", defaults={
            "nome": "Aprovação de pedido de compra",
            "descricao": "Coordenador, gerente e diretor do departamento, conforme o valor do pedido."})
        if criado:
            for ordem, nivel in enumerate(("Coordenador", "Gerente", "Diretor"), start=1):
                Etapa.objects.create(processo=proc4, ordem=ordem, nome=f"Aprovação do {nivel.lower()}",
                                     tipo="aprovacao", prazo_dias=2,
                                     instrucoes="Confira fornecedor, preços contra a cotação, quantidades e "
                                                "centro de custo.")
        # Versão anterior (alçada por variáveis de ambiente): fica inativa; pedidos em curso terminam nela.
        Processo.objects.filter(codigo__in=["aprovacao-compra", "aprovacao-compra-diretoria"]).update(ativo=False)

        proc3, criado = Processo.objects.get_or_create(codigo="aprovacao-contrato", defaults={
            "nome": "Aprovação de contrato",
            "descricao": "Contratos passam pela análise jurídica e pela diretoria antes da assinatura."})
        if criado:
            Etapa.objects.create(processo=proc3, ordem=1, nome="Análise jurídica", tipo="aprovacao",
                                 grupo_responsavel=grupos["Jurídico"], prazo_dias=3,
                                 instrucoes="Confira partes, objeto, vigência, valores, multa e cláusulas de rescisão.")
            Etapa.objects.create(processo=proc3, ordem=2, nome="Aprovação da diretoria", tipo="aprovacao",
                                 grupo_responsavel=grupos["Administradores"], prazo_dias=2)

        for nome, grupo in (("Jurídico", "Jurídico"), ("Contratos", "Administradores"), ("Recursos Humanos", "RH"), ("Fiscal", None),
                            ("Políticas e procedimentos", None)):
            pasta, criada = Pasta.objects.get_or_create(nome=nome, superior=None)
            if criada and grupo:
                pasta.grupos_acesso.add(grupos[grupo])
                if nome == "Jurídico":
                    pasta.grupos_acesso.add(grupos["Administradores"])

        self.stdout.write(self.style.SUCCESS("Configuração inicial concluída."))
