"""Telas de cadastro: empresa, departamentos, cargos, colaboradores, fornecedores e clientes."""
from .crud import Crud
from .models import Cargo, Cliente, Colaborador, Departamento, Empresa, Fornecedor, LogAuditoria

ENDERECO = ["cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"]


class EmpresaCrud(Crud):
    model = Empresa
    fields = ["razao_social", "nome_fantasia", "cnpj", "inscricao_estadual", "inscricao_municipal",
              "regime_tributario", "telefone", "email", "chave_pix", "matriz"] + ENDERECO
    list_display = ("razao_social", "nome_fantasia", "cnpj", "regime_tributario", "matriz")
    search_fields = ("razao_social", "nome_fantasia", "cnpj")
    icon = "building"
    menu = "Cadastros"
    menu_order = 1
    related = (("departamento_set", "Departamentos", ["nome", "responsavel"]),)


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
              "chave_pix", *ENDERECO, "ativo", "observacoes"]
    list_display = ("nome", "nome_fantasia", "documento_formatado", "categoria", "telefone", "ativo")
    search_fields = ("nome", "nome_fantasia", "documento", "email")
    filter_fields = ("categoria", "ativo", "tipo_pessoa")
    icon = "truck"
    menu = "Cadastros"
    menu_order = 5
    related = (("contas_pagar", "Contas a pagar", ["descricao", "vencimento", "valor", "status"]),)


class ClienteCrud(Crud):
    model = Cliente
    fields = ["tipo_pessoa", "nome", "nome_fantasia", "documento", "inscricao_estadual", "email",
              "email_cobranca", "telefone", "contato", "limite_credito", *ENDERECO, "ativo", "observacoes"]
    list_display = ("nome", "nome_fantasia", "documento_formatado", "email", "telefone", "ativo")
    search_fields = ("nome", "nome_fantasia", "documento", "email")
    filter_fields = ("ativo", "tipo_pessoa", "uf")
    icon = "person-vcard"
    menu = "Cadastros"
    menu_order = 6
    related = (("contas_receber", "Contas a receber", ["descricao", "vencimento", "valor", "status"]),)


class LogAuditoriaCrud(Crud):
    model = LogAuditoria
    list_display = ("data", "usuario", "acao", "modelo", "objeto_id", "descricao")
    search_fields = ("descricao", "modelo", "usuario__username")
    filter_fields = ("acao", "usuario")
    icon = "shield-check"
    menu = "Administração"
    menu_label = "Auditoria"
    menu_order = 90
    can_create = can_edit = can_delete = False
