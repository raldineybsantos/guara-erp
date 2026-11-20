"""Motor genérico de telas CRUD.

Cada módulo declara classes `Crud` (em seu arquivo crud.py) dizendo quais
campos listar, filtrar e editar. Daqui saem automaticamente: listagem com
busca, filtros, ordenação, paginação e exportação CSV; formulário de
inclusão/edição (com itens em grade, quando houver); tela de detalhe; e
exclusão — tudo respeitando as permissões do Django.
"""
import csv
import datetime
from decimal import Decimal

import django_filters
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import models, transaction
from django.db.models import ProtectedError, Q
from django.db.models.fields.files import FieldFile
from django.forms import inlineformset_factory
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import capfirst
from django.views import generic

REGISTRO = []  # todas as classes Crud registradas (usado pelo menu)


# --------------------------------------------------------------------------- formatação
def moeda(valor):
    if valor is None or valor == "":
        return ""
    return "R$ " + number_format(Decimal(valor), 2, force_grouping=True)


CORES_STATUS = (  # avaliadas nesta ordem
    ("danger", ("vencido", "rejeit", "recusad", "cancel", "deslig", "afastado", "rescindido", "atrasad")),
    ("warning", ("parcial", "andamento", "enviada", "férias", "em aprova", "aguardando", "vence em", "futura", "revis")),
    ("success", ("pago", "paga", "conclu", "emitida", "ativo", "aprov", "vigente")),
)


def badge(texto):
    t = str(texto).lower()
    cor = next((c for c, chaves in CORES_STATUS if any(k in t for k in chaves)), "secondary")
    return format_html('<span class="badge text-bg-{}">{}</span>', cor, texto)


def valor_exibicao(obj, nome, html=True):
    """Valor legível de um campo/propriedade/método de `obj`."""
    if html and nome in ("status", "situacao"):
        return badge(valor_exibicao(obj, nome, html=False))
    if callable(nome):
        return nome(obj)
    alvo = obj
    for parte in nome.split("__"):
        if alvo is None:
            return ""
        try:
            campo = alvo._meta.get_field(parte)
        except Exception:
            campo = None
        if campo is not None and getattr(campo, "choices", None):
            alvo = getattr(alvo, f"get_{parte}_display")()
            continue
        alvo = getattr(alvo, parte, "")
        if callable(alvo) and not isinstance(alvo, models.Model) and not hasattr(alvo, "all"):
            alvo = alvo()
    valor = alvo
    if valor is None:
        return ""
    if isinstance(valor, bool):
        if not html:
            return "Sim" if valor else "Não"
        return format_html('<i class="bi bi-{}"></i>', "check-circle-fill text-success" if valor else "x-circle text-muted")
    if isinstance(valor, datetime.datetime):
        if timezone.is_aware(valor):
            valor = timezone.localtime(valor)
        return date_format(valor, "d/m/Y H:i")
    if isinstance(valor, datetime.date):
        return date_format(valor, "d/m/Y")
    if isinstance(valor, Decimal):
        nome_base = nome.split("__")[-1] if isinstance(nome, str) else ""
        if nome_base.startswith(("aliquota", "percentual", "quantidade")):
            return number_format(valor, 2, force_grouping=True)
        return moeda(valor)
    if hasattr(valor, "all") and hasattr(valor, "model"):  # many-to-many
        return ", ".join(str(v) for v in valor.all())
    if isinstance(valor, FieldFile):  # arquivo
        if not valor:
            return ""
        return format_html('<a href="{}">{}</a>', valor.url, valor.name.split("/")[-1]) if html else valor.name
    if isinstance(valor, models.Model) and hasattr(valor, "get_full_name"):  # usuário
        return valor.get_full_name() or valor.get_username()
    if html and isinstance(valor, models.Model) and hasattr(valor, "get_absolute_url"):
        url = valor.get_absolute_url()
        if url and url != "#":
            return format_html('<a href="{}">{}</a>', url, valor)
    return valor


def celula_csv(valor):
    """Evita injeção de fórmulas no Excel/LibreOffice (=, +, -, @ no início da célula)."""
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        try:
            float(texto.replace(".", "").replace(",", "."))  # número negativo legítimo
        except ValueError:
            return "'" + texto
    return texto


def rotulo(model, nome):
    if callable(nome):
        return getattr(nome, "short_description", nome.__name__)
    alvo = model
    for parte in nome.split("__"):
        try:
            campo = alvo._meta.get_field(parte)
            if campo.is_relation and campo.related_model:
                alvo = campo.related_model
            texto = getattr(campo, "verbose_name", parte)
        except Exception:
            attr = getattr(alvo, parte, None)
            texto = getattr(attr, "short_description", None) or (
                getattr(getattr(attr, "fget", None), "short_description", None)) or parte.replace("_", " ")
    return str(texto)[:1].upper() + str(texto)[1:]


# --------------------------------------------------------------------------- formulários
class BootstrapMixin:
    """Aplica classes do Bootstrap e widgets adequados aos campos."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        emp = self.fields.get("empresa")
        if emp is not None and hasattr(emp, "queryset"):
            from .middleware import empresa_atual, usuario_atual
            from .models import Empresa

            if emp.queryset.model is Empresa and usuario_atual() is not None:
                emp.queryset = Empresa.acessiveis(usuario_atual())
                inst = getattr(self, "instance", None)
                if empresa_atual() is not None and (inst is None or not inst.pk):
                    self.initial.setdefault("empresa", empresa_atual().pk)
        for nome, campo in self.fields.items():
            w = campo.widget
            if isinstance(campo, forms.DateTimeField):
                campo.widget = w = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
            elif isinstance(campo, forms.DateField):
                campo.widget = w = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select")
            elif isinstance(w, forms.Textarea):
                w.attrs.setdefault("class", "form-control")
                w.attrs["rows"] = 3
            else:
                w.attrs.setdefault("class", "form-control")


class FormBase(BootstrapMixin, forms.ModelForm):
    pass


def form_para(model, fields, base=FormBase, exclude=None):
    meta_attrs = {"model": model}
    if fields:
        meta_attrs["fields"] = fields
    else:
        meta_attrs["exclude"] = exclude or ["criado_por"]
    Meta = type("Meta", (), meta_attrs)
    return type(f"{model.__name__}Form", (base,), {"Meta": Meta})


def restringir_por_empresa(qs, model, user, caminho="empresa"):
    """Registros com campo `empresa` (ou um `caminho` até ela, ex.: armazem__empresa) só aparecem para quem tem
    acesso àquela empresa (sem empresa: todos)."""
    from .models import Empresa

    try:
        campo = model._meta.get_field(caminho.split("__")[0])
    except Exception:
        return qs
    if caminho == "empresa" and getattr(campo, "related_model", None) is not Empresa:
        return qs
    if user.is_superuser:
        return qs
    if not Empresa.objects.filter(usuarios__isnull=False).exists():   # ninguém restringiu nada
        return qs
    return qs.filter(models.Q(**{f"{caminho}__in": Empresa.acessiveis(user)}) | models.Q(**{f"{caminho}__isnull": True}))


# --------------------------------------------------------------------------- Crud
class Crud:
    model = None
    fields = None                  # campos do formulário (None = todos editáveis)
    form_class = None
    list_display = ("__str__",)
    search_fields = ()
    filter_fields = ()
    ordering = None
    detail_fields = None           # None = todos
    related = ()                   # [(related_name, titulo, [campos])]
    inline = None                  # (ModelFilho, [campos], extra)
    inline_no_detalhe = True       # False quando `related` já mostra os filhos com mais colunas
    actions = ()                   # [(url_name, rótulo, ícone, permissão)] mostrados no detalhe
    acao_status = {}               # {url_name: (status, …)}: a ação só aparece nesses status do registro
    list_template = "crud/list.html"
    detail_template = "crud/detail.html"
    form_template = "crud/form.html"
    icon = "table"
    empresa_campo = "empresa"      # caminho até a empresa, para recortar a lista (ex.: "armazem__empresa")
    menu = None                    # grupo do menu lateral
    menu_label = None
    menu_order = 50
    paginate_by = 25
    can_create = True
    can_edit = True
    can_delete = True

    # ---------- registro
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.model is not None:
            REGISTRO.append(cls)

    @classmethod
    def meta(cls):
        return cls.model._meta

    @classmethod
    def nome(cls):
        return cls.meta().model_name

    @classmethod
    def app(cls):
        return cls.meta().app_label

    @classmethod
    def url_name(cls, acao):
        return f"{cls.app()}:{cls.nome()}_{acao}"

    @classmethod
    def perm(cls, acao):
        return f"{cls.app()}.{acao}_{cls.nome()}"

    @classmethod
    def get_form_class(cls):
        return cls.form_class or form_para(cls.model, cls.fields)

    @classmethod
    def get_queryset(cls, request):
        qs = cls.model._default_manager.all()
        qs = restringir_por_empresa(qs, cls.model, request.user, cls.empresa_campo)
        rel = [f.name for f in cls.meta().fields if f.is_relation and f.many_to_one]
        if rel:
            qs = qs.select_related(*rel)
        if cls.ordering:
            qs = qs.order_by(*cls.ordering)
        return qs

    filterset_class = None

    @classmethod
    def filterset(cls):
        if cls.filterset_class:
            return cls.filterset_class
        if not cls.filter_fields:
            return None
        campos = cls.filter_fields if isinstance(cls.filter_fields, dict) else list(cls.filter_fields)
        Meta = type("Meta", (), {"model": cls.model, "fields": campos})
        fs = type(f"{cls.model.__name__}Filter", (django_filters.FilterSet,), {"Meta": Meta})
        return fs

    @classmethod
    def urls(cls):
        n = cls.nome()
        return [
            path(f"{n}/", ListaView.as_view(crud=cls), name=f"{n}_list"),
            path(f"{n}/novo/", CriarView.as_view(crud=cls), name=f"{n}_create"),
            path(f"{n}/<int:pk>/", DetalheView.as_view(crud=cls), name=f"{n}_detail"),
            path(f"{n}/<int:pk>/editar/", EditarView.as_view(crud=cls), name=f"{n}_update"),
            path(f"{n}/<int:pk>/excluir/", ExcluirView.as_view(crud=cls), name=f"{n}_delete"),
        ]

    # ---------- ganchos opcionais
    @classmethod
    def initial(cls, request):
        return {k: v for k, v in request.GET.items() if k in {f.name for f in cls.meta().fields}}

    @classmethod
    def after_save(cls, request, obj, created):
        pass

    @classmethod
    def extra_detalhe(cls, request, obj):
        """Contexto adicional para templates de detalhe próprios."""
        return {}


def crud_urls(*cruds):
    padroes = []
    for c in cruds:
        padroes += c.urls()
    return padroes


# --------------------------------------------------------------------------- views
class CrudMixin(LoginRequiredMixin, PermissionRequiredMixin):
    crud = None
    acao_perm = "view"
    HABILITADO = {"add": "can_create", "change": "can_edit", "delete": "can_delete"}

    def get_permission_required(self):
        return [self.crud.perm(self.acao_perm)]

    def has_permission(self):
        # Ações desabilitadas na tela também ficam bloqueadas pela URL (não só escondidas).
        flag = self.HABILITADO.get(self.acao_perm)
        if flag and not getattr(self.crud, flag):
            return False
        return super().has_permission()

    def get_queryset(self):
        return self.crud.get_queryset(self.request)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        c, u = self.crud, self.request.user
        ctx.update({
            "crud": c,
            "meta": c.meta(),
            "titulo": capfirst(str(c.meta().verbose_name_plural)),
            "titulo_singular": capfirst(str(c.meta().verbose_name)),
            "icone": c.icon,
            "url_list": reverse(c.url_name("list")),
            "url_create": reverse(c.url_name("create")),
            "pode_criar": c.can_create and u.has_perm(c.perm("add")),
            "pode_editar": c.can_edit and u.has_perm(c.perm("change")),
            "pode_excluir": c.can_delete and u.has_perm(c.perm("delete")),
        })
        return ctx


class ListaView(CrudMixin, generic.ListView):
    def get_template_names(self):
        return [self.crud.list_template]

    def get_paginate_by(self, queryset):
        return None if self.request.GET.get("export") else self.crud.paginate_by

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q and self.crud.search_fields:
            cond = Q()
            for f in self.crud.search_fields:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond).distinct()
        fs_class = self.crud.filterset()
        self.filtro = None
        if fs_class:
            self.filtro = fs_class(self.request.GET, queryset=qs)
            emp = self.filtro.form.fields.get("empresa")
            if emp is not None and hasattr(emp, "queryset"):
                from .models import Empresa

                if emp.queryset.model is Empresa:   # o filtro não revela empresas sem acesso
                    emp.queryset = Empresa.acessiveis(self.request.user)
            for nome, campo in self.filtro.form.fields.items():
                if nome.endswith("__gte"):
                    campo.label = f"{rotulo(self.crud.model, nome[:-5])} de"
                elif nome.endswith("__lte"):
                    campo.label = "até"
                if isinstance(campo.widget, forms.NullBooleanSelect):
                    campo.widget = forms.Select(choices=[("", "Todos"), ("true", "Sim"), ("false", "Não")])
                campo.widget.attrs.setdefault(
                    "class", "form-select form-select-sm" if isinstance(campo.widget, forms.Select) else "form-control form-control-sm")
                if isinstance(campo.widget, forms.DateInput):
                    campo.widget.input_type = "date"
            qs = self.filtro.qs
        ordem = self.request.GET.get("o")
        if ordem:
            campo = ordem.lstrip("-")
            if campo in {f.name for f in self.crud.meta().fields}:
                qs = qs.order_by(ordem)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            return self.exportar_csv()
        return super().get(request, *args, **kwargs)

    def colunas(self):
        cols = []
        nomes_campos = {f.name for f in self.crud.meta().fields}
        for nome in self.crud.list_display:
            if nome == "__str__":
                cols.append(("__str__", str(self.crud.meta().verbose_name).capitalize(), False))
            else:
                cols.append((nome, rotulo(self.crud.model, nome), isinstance(nome, str) and nome in nomes_campos))
        return cols

    def exportar_csv(self):
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{self.crud.nome()}.csv"'
        resp.write("﻿")  # BOM para o Excel abrir acentos corretamente
        w = csv.writer(resp, delimiter=";")
        cols = self.colunas()
        w.writerow([c[1] for c in cols])
        n = 0
        for obj in self.get_queryset():
            n += 1
            w.writerow([celula_csv(str(obj) if c[0] == "__str__" else valor_exibicao(obj, c[0], html=False))
                        for c in cols])
        from .models import LogAuditoria

        LogAuditoria.objects.create(
            acao="exportar", modelo=self.crud.model._meta.label_lower,
            descricao=f"Exportou CSV de {self.crud.nome()} ({n} linha(s))",
            alteracoes={"filtros": sorted(k for k in self.request.GET if k != "export")})
        return resp

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cols = self.colunas()
        ctx["colunas"] = cols
        ctx["linhas"] = [
            (obj, [str(obj) if c[0] == "__str__" else valor_exibicao(obj, c[0]) for c in cols])
            for obj in ctx["object_list"]
        ]
        ctx["filtro"] = self.filtro
        ctx["q"] = self.request.GET.get("q", "")
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["params"] = params.urlencode()
        params.pop("o", None)
        ctx["params_sem_ordem"] = params.urlencode()
        ctx["ordem"] = self.request.GET.get("o", "")
        return ctx


class DetalheView(CrudMixin, generic.DetailView):
    def get_template_names(self):
        return [self.crud.detail_template, "crud/detail.html"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        obj = self.object
        c = self.crud
        nomes = c.detail_fields or [
            f.name for f in c.meta().fields if f.name not in ("id",) and not f.name.endswith("_ptr")
        ]
        ctx["campos"] = [(rotulo(c.model, n), valor_exibicao(obj, n)) for n in nomes]
        if c.model._meta.label_lower in getattr(settings, "AUDITORIA_LEITURA", ()):
            from .models import LogAuditoria

            LogAuditoria.registrar("ler", obj, descricao=f"Consultou {c.model._meta.verbose_name}: {obj}")
        relacionados = []
        for rel_name, titulo, campos in c.related:
            mgr = getattr(obj, rel_name)
            itens = list(mgr.all()[:100])
            rel_model = mgr.model
            relacionados.append({
                "titulo": titulo,
                "colunas": [rotulo(rel_model, n) for n in campos],
                "linhas": [(i, [valor_exibicao(i, n) for n in campos]) for i in itens],
            })
        if c.inline and c.inline_no_detalhe:
            filho, campos, _extra = c.inline
            fk = next(f for f in filho._meta.fields if f.is_relation and f.related_model is c.model)
            itens = filho._default_manager.filter(**{fk.name: obj})
            relacionados.insert(0, {
                "titulo": str(filho._meta.verbose_name_plural).capitalize(),
                "colunas": [rotulo(filho, n) for n in campos],
                "linhas": [(i, [valor_exibicao(i, n) for n in campos]) for i in itens],
            })
        ctx["relacionados"] = relacionados
        u = self.request.user
        ctx["acoes"] = [
            {"url": reverse(nome, args=[obj.pk]), "rotulo": rot, "icone": ico}
            for nome, rot, ico, perm in c.actions
            if (not perm or u.has_perm(perm))
            and (nome not in c.acao_status or getattr(obj, "status", None) in c.acao_status[nome])
        ]
        ctx.update(c.extra_detalhe(self.request, obj))
        from .signals import _auditado

        if _auditado(c.model) and u.has_perm("core.view_logauditoria"):
            ctx["acoes"].append({"url": reverse("core:historico", args=[c.app(), c.nome(), obj.pk]),
                                 "rotulo": "Histórico", "icone": "clock-history"})
        ctx["url_update"] = reverse(c.url_name("update"), args=[obj.pk])
        ctx["url_delete"] = reverse(c.url_name("delete"), args=[obj.pk])
        return ctx


class FormMixin:
    def get_template_names(self):
        return [self.crud.form_template, "crud/form.html"]

    def get_form_class(self):
        return self.crud.get_form_class()

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        form_cls = self.get_form_class()
        if getattr(form_cls, "precisa_request", False):
            kw["request"] = self.request
        return kw

    def get_formset_class(self):
        if not self.crud.inline:
            return None
        filho, campos, extra = self.crud.inline
        base = getattr(self.crud, "inline_form", None) or form_para(filho, campos)
        return inlineformset_factory(self.crud.model, filho, form=base, fields=campos, extra=extra, can_delete=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        fs_class = self.get_formset_class()
        if fs_class:
            ctx["formset_titulo"] = self.crud.inline[0]._meta.verbose_name_plural
        if fs_class and "formset" not in kwargs:
            ctx["formset"] = fs_class(self.request.POST or None, self.request.FILES or None,
                                      instance=getattr(self, "object", None))
        return ctx

    def form_valid(self, form):
        fs_class = self.get_formset_class()
        criado = getattr(self, "object", None) is None
        with transaction.atomic():
            if fs_class:
                obj = form.save(commit=False)
                formset = fs_class(self.request.POST, self.request.FILES, instance=obj)
                if not formset.is_valid():
                    transaction.set_rollback(True)
                    return self.render_to_response(self.get_context_data(form=form, formset=formset))
                obj.save()
                form.save_m2m()
                formset.instance = obj
                formset.save()
                self.object = obj
            else:
                self.object = form.save()
            self.crud.after_save(self.request, self.object, criado)
        messages.success(self.request, f"{self.crud.meta().verbose_name.capitalize()} salvo com sucesso.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        if "_continuar" in self.request.POST:
            return reverse(self.crud.url_name("create"))
        nxt = self.request.GET.get("next")
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={self.request.get_host()},
                                                   require_https=self.request.is_secure()):
            return nxt
        return reverse(self.crud.url_name("detail"), args=[self.object.pk])


class CriarView(CrudMixin, FormMixin, generic.CreateView):
    acao_perm = "add"

    def get_initial(self):
        inicial = self.crud.initial(self.request)
        proposta = self.request.GET.get("ia")
        if proposta and proposta.isdigit():   # formulário pré-preenchido pelo assistente de IA
            from apps.assistente.models import SolicitacaoCadastro

            sc = SolicitacaoCadastro.objects.filter(pk=proposta, usuario=self.request.user,
                                                    alvo=f"{self.crud.app()}.{self.crud.nome()}").first()
            if sc:
                inicial = {**sc.dados, **inicial}
                SolicitacaoCadastro.objects.filter(pk=sc.pk, status="pendente").update(status="encaminhada")
        return inicial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["subtitulo"] = f"Novo(a) {self.crud.meta().verbose_name}"
        return ctx


class EditarView(CrudMixin, FormMixin, generic.UpdateView):
    acao_perm = "change"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["subtitulo"] = f"Editar {self.crud.meta().verbose_name}: {self.object}"
        return ctx


class ExcluirView(CrudMixin, generic.DeleteView):
    acao_perm = "delete"
    template_name = "crud/confirm_delete.html"

    def get_success_url(self):
        return reverse(self.crud.url_name("list"))

    def form_valid(self, form):
        try:
            resp = super().form_valid(form)
            messages.success(self.request, "Registro excluído.")
            return resp
        except ProtectedError:
            messages.error(self.request, "Não é possível excluir: existem registros vinculados a este item.")
            return redirect(reverse(self.crud.url_name("detail"), args=[self.object.pk]))
