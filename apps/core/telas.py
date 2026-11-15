"""Telas de cadastro: empresa, departamentos, cargos, colaboradores, fornecedores e clientes."""
from .crud import Crud
from .models import (Cargo, Cliente, Colaborador, Departamento, Empresa, Fornecedor, LogAuditoria, LogErro,
                     ParceiroNegocio)

ENDERECO = ["cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"]
ENDERECO_PARCEIRO = [*ENDERECO, "codigo_municipio"]


class EmpresaCrud(Crud):
    model = Empresa
    fields = ["razao_social", "nome_fantasia", "cnpj", "inscricao_estadual", "inscricao_municipal",
              "regime_tributario", "cnae", "telefone", "email", "chave_pix", "matriz", "matriz_de"] + ENDERECO + [
                  "codigo_municipio", "usuarios", "ativa"]
    list_display = ("razao_social", "nome_fantasia", "cnpj", "regime_tributario", "matriz_de", "cidade", "ativa")
    filter_fields = ("regime_tributario", "ativa")
    search_fields = ("razao_social", "nome_fantasia", "cnpj")
    icon = "building"
    menu = "Cadastros"
    menu_order = 1
    related = (("filiais", "Filiais", ["razao_social", "cnpj", "cidade", "uf"]),
               ("departamento_set", "Departamentos", ["nome", "responsavel"]))


class DepartamentoCrud(Crud):
    model = Departamento
    fields = ["empresa", "codigo", "nome", "superior", "responsavel", "ativo"]
    list_display = ("codigo", "nome", "superior", "responsavel", "empresa", "ativo")
    search_fields = ("nome", "codigo")
    filter_fields = ("empresa", "ativo")
    icon = "diagram-3"
    menu = "Cadastros"
    menu_order = 2
    related = (
        ("colaboradores", "Colaboradores", ["nome", "cargo", "status"]),
        ("subdepartamentos", "Subdepartamentos", ["codigo", "nome"]),
    )


class CargoCrud(Crud):
    model = Cargo
    fields = ["nome", "cbo", "salario_base", "descricao"]
    list_display = ("nome", "cbo", "salario_base")
    search_fields = ("nome", "cbo")
    icon = "award"
    menu = "Cadastros"
    menu_order = 3
    related = (("colaboradores", "Colaboradores", ["nome", "departamento", "status"]),)


class ColaboradorCrud(Crud):
    model = Colaborador
    fields = ["matricula", "nome", "cpf", "rg", "data_nascimento", "email", "telefone", "empresa",
              "departamento", "cargo", "gestor", "tipo_contrato", "status", "data_admissao",
              "data_desligamento", "salario", "banco", "agencia", "conta", "chave_pix", "usuario",
              *ENDERECO, "observacoes"]
    list_display = ("matricula", "nome", "cpf_formatado", "departamento", "cargo", "tipo_contrato", "status")
    search_fields = ("nome", "cpf", "matricula", "email")
    filter_fields = ("departamento", "cargo", "status", "tipo_contrato")
    icon = "people"
    menu = "Cadastros"
    menu_order = 4
    related = (("liderados", "Liderados", ["nome", "cargo"]),)


class FornecedorCrud(Crud):
    model = Fornecedor
    fields = ["tipo_pessoa", "nome", "nome_fantasia", "documento", "inscricao_estadual", "categoria",
              "email", "telefone", "contato", "prazo_pagamento_dias", "banco", "agencia", "conta",
              "chave_pix", *ENDERECO_PARCEIRO, "ativo", "observacoes"]
    list_display = ("nome", "nome_fantasia", "documento_formatado", "categoria", "telefone", "ativo")
    search_fields = ("nome", "nome_fantasia", "documento", "email")
    filter_fields = ("categoria", "ativo", "tipo_pessoa")
    icon = "truck"
    menu = "Cadastros"
    menu_order = 5
    actions = (("core:tornar_cliente", "Tornar cliente", "person-plus", "core.add_cliente"),)
    related = (("contas_pagar", "Contas a pagar", ["descricao", "vencimento", "valor", "status"]),)


class ClienteCrud(Crud):
    model = Cliente
    fields = ["tipo_pessoa", "nome", "nome_fantasia", "documento", "inscricao_estadual", "email",
              "email_cobranca", "telefone", "contato", "limite_credito", *ENDERECO_PARCEIRO, "ativo", "observacoes"]
    list_display = ("nome", "nome_fantasia", "documento_formatado", "email", "telefone", "ativo")
    search_fields = ("nome", "nome_fantasia", "documento", "email")
    filter_fields = ("ativo", "tipo_pessoa", "uf")
    icon = "person-vcard"
    menu = "Cadastros"
    menu_order = 6
    actions = (("core:tornar_fornecedor", "Tornar fornecedor", "truck", "core.add_fornecedor"),)
    related = (("contas_receber", "Contas a receber", ["descricao", "vencimento", "valor", "status"]),)


class ParceiroNegocioCrud(Crud):
    """Visão única de clientes e fornecedores: cada CPF/CNPJ aparece uma vez, com seus papéis."""

    model = ParceiroNegocio
    fields = ["tipo_pessoa", "nome", "nome_fantasia", "documento", "inscricao_estadual", "email", "telefone",
              "contato", *ENDERECO_PARCEIRO, "ativo", "observacoes"]
    list_display = ("nome", "nome_fantasia", "documento_formatado", "papeis_texto", "cidade", "ativo")
    search_fields = ("nome", "nome_fantasia", "documento", "email")
    filter_fields = ("ativo", "tipo_pessoa", "uf")
    icon = "people-fill"
    menu = "Cadastros"
    menu_label = "Parceiros de negócios"
    menu_order = 7
    can_create = False   # nasce como cliente ou fornecedor
    can_delete = False   # exclui-se o papel, não o cadastro-mãe


class LogAuditoriaCrud(Crud):
    model = LogAuditoria
    list_display = ("data", "usuario_nome", "acao", "modelo", "objeto_id", "descricao", "ip")
    search_fields = ("descricao", "modelo", "usuario_nome", "objeto_id", "requisicao", "ip")
    filter_fields = {"acao": ["exact"], "usuario": ["exact"], "empresa": ["exact"], "data": ["gte", "lte"]}
    detail_fields = ["data", "usuario_nome", "empresa", "acao", "modelo", "objeto_id", "descricao", "ip", "caminho",
                     "requisicao", "agente", "hash"]
    detail_template = "core/log_detalhe.html"
    icon = "shield-check"
    menu = "Administração"
    menu_label = "Auditoria"
    menu_order = 90
    can_create = can_edit = can_delete = False


class LogErroCrud(Crud):
    model = LogErro
    list_display = ("ultima_em", "situacao", "tipo", "mensagem", "caminho", "ocorrencias", "usuario_nome")
    search_fields = ("tipo", "mensagem", "caminho", "requisicao", "usuario_nome")
    filter_fields = {"situacao": ["exact"], "origem": ["exact"], "reincidente": ["exact"],
                     "ultima_em": ["gte", "lte"]}
    detail_fields = ["situacao", "tipo", "mensagem", "ocorrencias", "criado_em", "ultima_em", "reincidente",
                     "origem", "nivel", "metodo", "caminho", "status_http", "usuario_nome", "empresa", "ip",
                     "requisicao", "tratado_por", "tratado_em"]
    detail_template = "core/log_detalhe.html"
    actions = (("core:erro_analisar", "Em análise", "search", "core.change_logerro"),
               ("core:erro_resolver", "Resolvido", "check2-circle", "core.change_logerro"),
               ("core:erro_ignorar", "Ignorar", "eye-slash", "core.change_logerro"))
    icon = "bug"
    menu = "Administração"
    menu_label = "Logs de erro"
    menu_order = 92
    can_create = can_edit = can_delete = False
