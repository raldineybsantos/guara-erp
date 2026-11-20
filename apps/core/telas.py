"""Telas de cadastro: empresa, departamentos, cargos, colaboradores, fornecedores e clientes."""
import re

from django import forms
from django.utils import timezone

from .crud import Crud, FormBase
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


OBRIGATORIOS_COLABORADOR = {
    "rg": "Informe o RG.", "data_nascimento": "Informe a data de nascimento.", "email": "Informe o e-mail.",
    "telefone": "Informe o telefone.", "departamento": "Informe o departamento.", "cargo": "Informe o cargo.",
    "cep": "Informe o CEP.", "logradouro": "Informe o logradouro.", "numero": "Informe o número (ou S/N).",
    "bairro": "Informe o bairro.", "cidade": "Informe a cidade.", "uf": "Informe a UF.",
}


class ColaboradorForm(FormBase):
    """Cadastro completo: o essencial do contrato, do endereço e dos dados de contato é obrigatório e conferido."""

    class Meta:
        model = Colaborador
        fields = ["matricula", "nome", "cpf", "rg", "data_nascimento", "email", "telefone", "empresa",
                  "departamento", "cargo", "gestor", "tipo_contrato", "status", "data_admissao",
                  "data_desligamento", "salario", "banco", "agencia", "conta", "chave_pix", "usuario",
                  *ENDERECO, "observacoes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and "gestor" in self.fields:   # ninguém é gestor de si mesmo
            self.fields["gestor"].queryset = self.fields["gestor"].queryset.exclude(pk=self.instance.pk)

    def clean(self):
        d = super().clean()
        hoje = timezone.localdate()
        for campo, msg in OBRIGATORIOS_COLABORADOR.items():
            if campo in self.fields and not d.get(campo) and campo not in self.errors:
                self.add_error(campo, msg)
        nasc, adm, desl = d.get("data_nascimento"), d.get("data_admissao"), d.get("data_desligamento")
        if nasc:
            idade = hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))
            if nasc > hoje:
                self.add_error("data_nascimento", "A data de nascimento não pode ser futura.")
            elif idade < 14:
                self.add_error("data_nascimento", "Colaborador com menos de 14 anos não pode ser cadastrado.")
            elif idade < 16 and d.get("tipo_contrato") != "estagio":
                self.add_error("data_nascimento", "Menor de 16 anos só pode ser admitido como aprendiz/estagiário.")
            elif idade > 100:
                self.add_error("data_nascimento", "Confira a data de nascimento.")
            elif adm and adm < nasc.replace(year=nasc.year + 14):
                self.add_error("data_admissao", "A admissão é anterior aos 14 anos do colaborador.")
        if d.get("salario") is not None and d["salario"] <= 0 and d.get("tipo_contrato") != "socio":
            self.add_error("salario", "Informe o salário (maior que zero).")
        gestor = d.get("gestor")
        if gestor and self.instance.pk:
            g, vistos = gestor, set()
            while g is not None and g.pk not in vistos:
                if g.pk == self.instance.pk:
                    self.add_error("gestor", "Esse gestor já é subordinado a este colaborador (ciclo na hierarquia).")
                    break
                vistos.add(g.pk)
                g = g.gestor
        if desl and adm and desl < adm:
            self.add_error("data_desligamento", "O desligamento não pode ser anterior à admissão.")
        if d.get("status") == "desligado" and not desl:
            self.add_error("data_desligamento", "Informe a data de desligamento.")
        if desl and d.get("status") != "desligado":
            self.add_error("status", "Há data de desligamento: o status deve ser Desligado.")
        tel = re.sub(r"\D", "", d.get("telefone") or "")
        if d.get("telefone") and len(tel) not in (10, 11):
            self.add_error("telefone", "Telefone inválido: informe o DDD e o número (10 ou 11 dígitos).")
        cep = re.sub(r"\D", "", d.get("cep") or "")
        if d.get("cep") and len(cep) != 8:
            self.add_error("cep", "CEP inválido: informe os 8 dígitos.")
        if any(d.get(c) for c in ("banco", "agencia", "conta")) and not all(d.get(c) for c in ("banco", "agencia", "conta")):
            self.add_error("conta", "Preencha banco, agência e conta juntos (ou deixe os três em branco e use o PIX).")
        if not any(d.get(c) for c in ("conta", "chave_pix")) and d.get("status") == "ativo":
            self.add_error("chave_pix", "Informe uma conta bancária ou a chave PIX para pagamento do salário.")
        return d


class ColaboradorCrud(Crud):
    model = Colaborador
    form_class = ColaboradorForm
    detail_template = "rh/colaborador_detail.html"
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

    @classmethod
    def extra_detalhe(cls, request, obj):
        if not request.user.has_perm("rh.view_documentopessoal"):
            return {"pendencias_docs": None}
        from apps.rh.documentos_pessoais import pendencias
        return {"pendencias_docs": pendencias(obj)}
    related = (("dependentes", "Dependentes", ["nome", "parentesco", "data_nascimento"]),
               ("documentos_pessoais", "Documentos anexados", ["tipo", "dependente", "validade"]),
               ("liderados", "Liderados", ["nome", "cargo"]))


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
