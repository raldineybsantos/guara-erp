"""Catálogo do que pode ser cadastrado: todas as telas registradas no ERP em que o USUÁRIO tem permissão de
incluir. O assistente só enxerga e só propõe o que está aqui, com os campos do formulário de cada tela."""

from django import forms

from apps.core.crud import REGISTRO

IGNORAR = ("assistente.",)


def _carregar_telas():
    """As telas se registram quando as URLs são importadas (fora de uma requisição isso pode não ter ocorrido)."""
    from importlib import import_module

    from django.conf import settings

    import_module(settings.ROOT_URLCONF)


def crud_de(chave):
    _carregar_telas()
    for c in REGISTRO:
        if f"{c.app()}.{c.nome()}" == chave:
            return c
    return None


def disponiveis(user):
    _carregar_telas()
    itens = []
    for c in REGISTRO:
        chave = f"{c.app()}.{c.nome()}"
        if c.can_create and user.has_perm(c.perm("add")) and not chave.startswith(IGNORAR):
            itens.append((chave, str(c.meta().verbose_name), c))
    return itens


def formulario(crud, dados=None):
    cls = crud.get_form_class()
    try:
        return cls(data=dados) if dados is not None else cls()
    except TypeError:
        return cls(data=dados, request=None) if dados is not None else cls(request=None)


def campos(crud):
    """[{nome, rotulo, tipo, obrigatorio, opcoes}] dos campos que a IA pode preencher."""
    saida = []
    for nome, f in formulario(crud).fields.items():
        if isinstance(f, (forms.FileField, forms.ModelMultipleChoiceField)) or f.disabled:
            continue
        item = {"nome": nome, "rotulo": str(f.label or nome), "obrigatorio": f.required}
        if isinstance(f, forms.ModelChoiceField):
            item["tipo"] = f"nome de {f.queryset.model._meta.verbose_name} já cadastrado"
        elif getattr(f, "choices", None):
            item["tipo"] = "opção"
            item["opcoes"] = [str(v) for v, _ in f.choices if v != ""][:40]
        elif isinstance(f, forms.BooleanField):
            item["tipo"] = "true/false"
        elif isinstance(f, forms.DateTimeField):
            item["tipo"] = "data e hora AAAA-MM-DDTHH:MM"
        elif isinstance(f, forms.DateField):
            item["tipo"] = "data AAAA-MM-DD"
        elif isinstance(f, (forms.DecimalField, forms.IntegerField, forms.FloatField)):
            item["tipo"] = "número (ponto decimal)"
        else:
            item["tipo"] = "texto"
        saida.append(item)
    return saida


def resolver_relacoes(form, dados):
    """Troca nomes por IDs nos campos de relação. Devolve as pendências (não achou / achou vários)."""
    pendencias = []
    for nome, valor in list(dados.items()):
        f = form.fields.get(nome)
        if not isinstance(f, forms.ModelChoiceField) or valor in (None, ""):
            continue
        texto = str(valor).strip().lower()
        exatos, parciais = [], []
        for obj in f.queryset[:3000]:
            rotulo = str(f.label_from_instance(obj)).lower()
            if rotulo == texto or str(obj.pk) == texto:
                exatos.append(obj)
            elif texto in rotulo:
                parciais.append(obj)
        achados = exatos or parciais
        if len(achados) == 1:
            dados[nome] = achados[0].pk
        else:
            dados.pop(nome)
            motivo = "não encontrei" if not achados else f"há {len(achados)} com esse nome"
            pendencias.append(f"{f.label or nome}: {motivo} “{valor}”. Escolha no formulário.")
    return pendencias
