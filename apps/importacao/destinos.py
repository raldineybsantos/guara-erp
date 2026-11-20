"""Tabelas do Guará que aceitam importação e seus campos. Cada destino lista só os campos que fazem sentido importar;
tipos, rótulos e opções vêm dos próprios modelos, então o que mudar no cadastro vale aqui também."""
from django.db import models

from . import conversao as cv

SINONIMOS = {
    "nome": ["nome", "nomecompleto", "funcionario", "colaborador", "razaosocial", "name", "cliente", "fornecedor",
             "nomedofuncionario", "nomedocolaborador"],
    "cpf": ["cpf", "cpfdofuncionario", "numerocpf"],
    "documento": ["cpfcnpj", "cnpjcpf", "documento", "cnpj", "cpf", "doc", "numerodocumento"],
    "matricula": ["matricula", "mat", "registro", "chapa", "codigofuncionario"],
    "rg": ["rg", "identidade", "registrogeral"],
    "data_nascimento": ["nascimento", "datanascimento", "datadenascimento", "dtnascimento", "dtnasc", "nasc"],
    "email": ["email", "emails", "correioeletronico", "mail"],
    "telefone": ["telefone", "fone", "celular", "tel", "whatsapp", "contato", "phone"],
    "departamento": ["departamento", "depto", "setor", "area", "lotacao", "dept"],
    "cargo": ["cargo", "funcao", "ocupacao", "posicao", "titulo"],
    "tipo_contrato": ["tipocontrato", "contrato", "vinculo", "regime", "tipodecontrato"],
    "data_admissao": ["admissao", "dataadmissao", "dtadmissao", "dataadmissao", "contratacao", "datadeadmissao"],
    "data_desligamento": ["desligamento", "datadesligamento", "demissao", "datademissao", "dtdesligamento"],
    "salario": ["salario", "remuneracao", "salariobase", "vencimento", "salarioatual"],
    "status": ["status", "situacao", "situacaofuncionario"],
    "banco": ["banco"], "agencia": ["agencia", "ag"], "conta": ["conta", "contacorrente", "cc"],
    "chave_pix": ["pix", "chavepix"],
    "cep": ["cep", "codigopostal"], "logradouro": ["logradouro", "endereco", "rua", "avenida"],
    "numero": ["numero", "num", "nr", "n"], "complemento": ["complemento", "compl"], "bairro": ["bairro", "distrito"],
    "cidade": ["cidade", "municipio", "localidade"], "uf": ["uf", "estado", "sigla"],
    "observacoes": ["observacoes", "obs", "observacao", "anotacoes", "notas", "comentarios"],
    "nome_fantasia": ["fantasia", "nomefantasia", "apelido"],
    "inscricao_estadual": ["ie", "inscricaoestadual", "inscestadual", "inscest"],
    "tipo_pessoa": ["tipopessoa", "pessoa", "fisicajuridica", "pfpj", "tipo"],
    "contato": ["contato", "responsavel", "pessoadecontato"],
    "ativo": ["ativo", "ativa", "ativado", "habilitado"],
    "codigo": ["codigo", "cod", "sku", "referencia", "ref", "codigoproduto", "codproduto", "id"],
    "descricao": ["descricao", "produto", "nomeproduto", "item", "descricaoproduto", "descr"],
    "tipo": ["tipo", "tipoitem", "categoria"],
    "unidade": ["unidade", "un", "und", "unidademedida", "um"],
    "ncm": ["ncm"], "cest": ["cest"], "cfop_padrao": ["cfop"],
    "preco": ["preco", "precovenda", "valor", "valorunitario", "precounitario"],
    "estoque_minimo": ["estoqueminimo", "minimo", "estmin"],
    "superior": ["superior", "departamentosuperior", "pai", "deptosuperior"],
    "cbo": ["cbo"], "salario_base": ["salariobase", "salario", "piso"],
}


class Campo:
    def __init__(self, modelo, nome, obrigatorio=None):
        f = modelo._meta.get_field(nome)
        self.nome = nome
        self.rotulo = str(getattr(f, "verbose_name", nome)).capitalize()
        self.campo = f
        self.obrigatorio = (not f.blank and not f.null and not f.has_default()) if obrigatorio is None else obrigatorio
        self.choices = list(f.choices) if getattr(f, "choices", None) else None
        if nome == "cpf":
            self.tipo = "cpf"
        elif nome == "documento":
            self.tipo = "documento"
        elif f.is_relation:
            self.tipo = "fk"
        elif self.choices:
            self.tipo = "escolha"
        elif isinstance(f, models.BooleanField):
            self.tipo = "booleano"
        elif isinstance(f, models.DecimalField) or isinstance(f, models.FloatField):
            self.tipo = "decimal"
        elif isinstance(f, models.IntegerField):
            self.tipo = "inteiro"
        elif isinstance(f, models.DateTimeField) or isinstance(f, models.DateField):
            self.tipo = "data"
        elif isinstance(f, models.EmailField):
            self.tipo = "email"
        else:
            self.tipo = "texto"
        self.max_length = getattr(f, "max_length", None)
        self.sinonimos = SINONIMOS.get(nome, [])

    def descricao_ia(self):
        d = {"campo": self.nome, "rotulo": self.rotulo, "tipo": self.tipo, "obrigatorio": self.obrigatorio}
        if self.choices:
            d["opcoes"] = [str(r) for _, r in self.choices][:12]
        return d

    def converter(self, valor, contexto=None):
        """Valor bruto da origem → valor do tipo do campo. Levanta ValueError com mensagem amigável."""
        t = self.tipo
        if cv.vazio(valor):
            return None if (self.campo.null and t in ("data", "decimal", "inteiro")) else (
                False if t == "booleano" else ("" if t in ("texto", "email", "escolha", "cpf", "documento") else None))
        if t == "cpf":
            return cv.cpf(valor)
        if t == "documento":
            return cv.cnpj_ou_cpf(valor)
        if t == "data":
            return cv.data(valor)
        if t == "decimal":
            return cv.decimal(valor)
        if t == "inteiro":
            return cv.inteiro(valor)
        if t == "booleano":
            return cv.booleano(valor)
        if t == "escolha":
            return cv.escolha(valor, self.choices, SINONIMOS_ESCOLHA.get(self.nome))
        if t == "email":
            return cv.texto(valor).lower()
        if t == "fk":
            return cv.texto(valor)           # resolvido pelo serviço (precisa do banco)
        v = cv.texto(valor)
        if self.nome in ("cep",):
            v = cv.so_digitos(v)
            v = v.zfill(8) if v else v
            return f"{v[:5]}-{v[5:]}" if len(v) == 8 else v
        if self.nome == "uf":
            return v.upper()
        if self.max_length and len(v) > self.max_length:
            raise ValueError(f"Texto com {len(v)} caracteres; o máximo é {self.max_length}.")
        return v


SINONIMOS_ESCOLHA = {
    "tipo_contrato": {"clt": "clt", "celetista": "clt", "pessoa juridica": "pj", "autonomo": "pj", "terceiro": "pj",
                      "estagiario": "estagio", "temp": "temporario", "temporario": "temporario"},
    "status": {"ativo": "ativo", "trabalhando": "ativo", "demitido": "desligado", "inativo": "desligado",
               "licenca": "afastado", "afastada": "afastado", "ferias": "ferias"},
    "tipo_pessoa": {"fisica": "PF", "pf": "PF", "f": "PF", "juridica": "PJ", "pj": "PJ", "j": "PJ"},
    "tipo": {"servico": "servico", "servicos": "servico", "produto": "produto", "mercadoria": "produto"},
}


class Destino:
    def __init__(self, chave, rotulo, modelo, campos, unicos, perm, descricao="", fks=None, por_empresa=False, papel=False):
        self.chave, self.rotulo, self.modelo = chave, rotulo, modelo
        self.campos = [Campo(modelo, c) for c in campos]
        self.por_nome = {c.nome: c for c in self.campos}
        self.unicos, self.perm, self.descricao = unicos, perm, descricao
        self.fks = fks or {}                 # campo → (modelo relacionado, campo de busca)
        self.por_empresa, self.papel = por_empresa, papel

    def pode(self, user):
        return user.has_perm(f"{self.perm}.add_{self.modelo._meta.model_name}")

    def pode_atualizar(self, user):
        return user.has_perm(f"{self.perm}.change_{self.modelo._meta.model_name}")


ENDERECO = ["cep", "logradouro", "numero", "complemento", "bairro", "cidade", "uf"]
_cache = {}


def destinos():
    """Montado na primeira chamada (os modelos precisam estar carregados)."""
    if _cache:
        return _cache
    from apps.core.models import Cargo, Colaborador, Departamento, ParceiroNegocio
    from apps.fiscal.models import Produto
    lista = [
        Destino("colaborador", "Colaboradores", Colaborador,
                ["nome", "cpf", "matricula", "rg", "data_nascimento", "email", "telefone", "departamento", "cargo",
                 "tipo_contrato", "data_admissao", "data_desligamento", "salario", "status", "banco", "agencia", "conta",
                 "chave_pix", *ENDERECO, "observacoes"], ["cpf"], "core",
                "Funcionários. Documentos obrigatórios (RG, comprovante…) ficam como pendência na ficha, para anexar depois.",
                fks={"departamento": (Departamento, "nome"), "cargo": (Cargo, "nome")}, por_empresa=True),
        Destino("parceiro", "Clientes e fornecedores", ParceiroNegocio,
                ["nome", "nome_fantasia", "tipo_pessoa", "documento", "inscricao_estadual", "email", "telefone", "contato",
                 *ENDERECO, "ativo", "observacoes"], ["documento"], "core",
                "Cadastro único de parceiros; escolha abaixo se também viram clientes, fornecedores ou ambos.", papel=True),
        Destino("produto", "Produtos e serviços", Produto,
                ["codigo", "descricao", "tipo", "unidade", "ncm", "cest", "cfop_padrao", "preco", "estoque_minimo"],
                ["codigo"], "fiscal", "Produtos e serviços do estoque e da nota fiscal."),
        Destino("departamento", "Departamentos", Departamento, ["codigo", "nome", "superior", "ativo"], ["nome"], "core",
                "Estrutura da empresa.", fks={"superior": (Departamento, "nome")}, por_empresa=True),
        Destino("cargo", "Cargos", Cargo, ["nome", "cbo", "salario_base", "descricao"], ["nome"], "core", "Cargos e salário base."),
    ]
    _cache.update({d.chave: d for d in lista})
    return _cache


def destino(chave):
    return destinos().get(chave)
