from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.core.crud import Crud, FormBase

from .models import Atividade


class AtividadeForm(FormBase):
    precisa_request = True

    class Meta:
        model = Atividade
        fields = ["titulo", "descricao", "status", "prioridade", "responsavel", "grupo", "departamento",
                  "prazo", "etiquetas"]

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["responsavel"].queryset = get_user_model().objects.filter(is_active=True).order_by("first_name", "username")
        self.fields["responsavel"].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields["grupo"].queryset = Group.objects.order_by("name")
        if request is not None and not self.instance.pk and "responsavel" not in self.initial and "grupo" not in self.initial:
            self.initial["responsavel"] = request.user.pk

    def clean(self):
        dados = super().clean()
        if not dados.get("responsavel") and not dados.get("grupo"):
            raise forms.ValidationError("Informe um responsável ou um grupo.")
        return dados


class AtividadeCrud(Crud):
    model = Atividade
    form_class = AtividadeForm
    list_display = ("titulo", "status", "prioridade", "responsavel", "grupo", "prazo", "concluida_em")
    search_fields = ("titulo", "descricao", "etiquetas")
    filter_fields = ("status", "prioridade", "grupo", "responsavel")
    detail_template = "tarefas/atividade_detail.html"
    detail_fields = ["titulo", "descricao", "status", "prioridade", "responsavel", "grupo", "departamento", "prazo",
                     "etiquetas", "criado_por", "criado_em", "iniciada_em", "concluida_em"]
    icon = "list-check"
    menu = "Principal"
    menu_label = "Lista de atividades"
    menu_order = 3

    @classmethod
    def get_queryset(cls, request):
        return Atividade.objects.visiveis_para(request.user).select_related("responsavel", "grupo", "departamento")

    @classmethod
    def after_save(cls, request, obj, created):
        if obj.status == "concluida" and not obj.concluida_em or obj.status == "fazendo" and not obj.iniciada_em:
            obj.mudar_status(obj.status)
            obj.save(update_fields=["concluida_em", "iniciada_em"])
