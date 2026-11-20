"""Primeiros passos: checklist de configuração inicial, com atalho para cada cadastro que falta."""
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse


def _passos():
    from apps.compras.models import AlcadaCompra
    from apps.contabilidade.models import ContaContabil, RegraContabil
    from apps.core.models import Cargo, Colaborador, Departamento, Empresa
    from apps.estoque.models import Armazem
    from apps.financeiro.models import ContaBancaria
    from apps.fiscal.models import Produto
    from apps.servicedesk.models import Area

    U = get_user_model()
    return [
        ("Empresa", "Razão social, CNPJ (use o botão Consultar CNPJ), regime tributário e código IBGE do município.",
         Empresa.objects.exists(), "core:empresa_create", "core.add_empresa"),
        ("Departamentos", "Estrutura da empresa; são usados em compras, RH e alçadas.",
         Departamento.objects.exists(), "core:departamento_create", "core.add_departamento"),
        ("Cargos", "Necessários para cadastrar colaboradores.", Cargo.objects.exists(), "core:cargo_create",
         "core.add_cargo"),
        ("Usuários e grupos", "Crie os usuários de cada área e coloque-os nos grupos de permissão (admin).",
         U.objects.filter(is_superuser=False).exists(), "admin:auth_user_add", "auth.add_user"),
        ("Colaboradores", "Cadastro de pessoas; ligue cada um ao seu usuário e departamento.",
         Colaborador.objects.exists(), "core:colaborador_create", "core.add_colaborador"),
        ("Plano de contas contábil e regras", "Já vem pronto com a configuração inicial; confira as regras de "
         "contabilização.", ContaContabil.objects.exists() and RegraContabil.objects.exists(),
         "contabilidade:regracontabil_list", "contabilidade.view_regracontabil"),
        ("Conta bancária", "Necessária para pagar e receber títulos.", ContaBancaria.objects.exists(),
         "financeiro:contabancaria_create", "financeiro.add_contabancaria"),
        ("Produtos e serviços", "Itens de compra, venda e estoque.", Produto.objects.exists(),
         "fiscal:produto_create", "fiscal.add_produto"),
        ("Armazém", "Local de estoque (o armazém padrão já existe).", Armazem.objects.exists(),
         "estoque:armazem_create", "estoque.add_armazem"),
        ("Alçada de compras", "Define quem aprova pedidos de compra e até quanto.", AlcadaCompra.objects.exists(),
         "compras:alcadacompra_create", "compras.add_alcadacompra"),
        ("Áreas de atendimento (Service Desk)", "Sem elas ninguém consegue abrir chamado.", Area.objects.exists(),
         "servicedesk:area_create", "servicedesk.add_area"),
    ]


def pendentes_para(user):
    return [p for p in _passos() if not p[2] and user.has_perm(p[4])]


@login_required
def primeiros_passos(request):
    itens = []
    for titulo, ajuda, feito, rota, perm in _passos():
        try:
            url = reverse(rota)
        except Exception:
            url = ""
        itens.append({"titulo": titulo, "ajuda": ajuda, "feito": feito, "url": url,
                      "pode": request.user.has_perm(perm)})
    feitos = sum(1 for i in itens if i["feito"])
    return render(request, "core/primeiros_passos.html", {
        "titulo": "Primeiros passos", "icone": "list-check", "itens": itens, "feitos": feitos, "total": len(itens),
        "percentual": round(100 * feitos / len(itens))})
