import datetime

import django_filters
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.utils import timezone

from apps.core.crud import Crud, FormBase
from apps.core.validators import validar_arquivo_seguro

from .models import Aditivo, Contrato

TERMOS_TRAVADOS = ["tipo", "fornecedor", "cliente", "outra_parte", "objeto", "valor_total", "valor_mensal",
                   "inicio_vigencia", "fim_vigencia", "indice_reajuste"]


def pasta_juridico():
    from apps.ged.models import Pasta

    pasta, criada = Pasta.objects.get_or_create(nome="Jurídico", superior=None)
    if criada:
        pasta.grupos_acesso.add(*Group.objects.filter(name__in=["Jurídico", "Administradores"]))
    return pasta


class ContratoForm(FormBase):
    precisa_request = True
    arquivo = forms.FileField(label="Arquivo do contrato (PDF recomendado)", required=False,
                              validators=[validar_arquivo_seguro],
                              help_text="Fica guardado no GED, na pasta Jurídico. Enviar outro arquivo cria uma nova versão.")

    class Meta:
        model = Contrato
        fields = ["titulo", "tipo", "categoria", "fornecedor", "cliente", "outra_parte", "objeto", "valor_total",
                  "valor_mensal", "indice_reajuste", "inicio_vigencia", "fim_vigencia", "renovacao_automatica",
                  "prazo_renovacao_meses", "aviso_previo_dias", "alerta_dias", "multa_rescisoria",
                  "assinaturas_necessarias", "responsavel", "departamento", "empresa", "observacoes"]

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.fields["responsavel"].queryset = get_user_model().objects.filter(is_active=True).order_by("first_name")
        self.fields["responsavel"].label_from_instance = lambda u: u.get_full_name() or u.username
        if request is not None and not self.instance.pk:
            self.initial.setdefault("responsavel", request.user.pk)
        if self.instance.pk and self.instance.status not in ("rascunho", "recusado"):
            for nome in TERMOS_TRAVADOS:
                self.fields[nome].disabled = True
                self.fields[nome].help_text = "Contrato aprovado: altere por aditivo."

    def save(self, commit=True):
        contrato = super().save(commit=commit)
        arquivo = self.cleaned_data.get("arquivo")
        if arquivo and commit:
            from apps.ged.models import Documento

            if contrato.documento_id is None:
                contrato.documento = Documento.objects.create(
                    pasta=pasta_juridico(), titulo=f"Contrato {contrato.numero} — {contrato.titulo}"[:200],
                    tipo="contrato", validade=contrato.fim_vigencia, tags=f"contrato, {contrato.numero}")
                contrato.save(update_fields=["documento"])
            contrato.documento.adicionar_versao(arquivo, "Arquivo do contrato", getattr(self.request, "user", None))
        return contrato


VENCIMENTO = [("30", "Vencem em até 30 dias"), ("60", "Vencem em até 60 dias"), ("90", "Vencem em até 90 dias"),
              ("vencidos", "Vencidos (ainda vigentes)")]


class ContratoFilter(django_filters.FilterSet):
    vencimento = django_filters.ChoiceFilter(label="Vencimento", choices=VENCIMENTO, method="filtrar_vencimento")

    class Meta:
        model = Contrato
        fields = ["status", "tipo", "categoria", "fornecedor", "cliente", "vencimento"]

    def filtrar_vencimento(self, qs, name, valor):
        hoje = timezone.localdate()
        if valor == "vencidos":
            return qs.filter(status="vigente", fim_vigencia__lt=hoje)
        return qs.filter(status="vigente", fim_vigencia__gte=hoje,
                         fim_vigencia__lte=hoje + datetime.timedelta(days=int(valor)))


class ContratoCrud(Crud):
    model = Contrato
    form_class = ContratoForm
    filterset_class = ContratoFilter
    list_display = ("numero", "titulo", "parte", "categoria", "inicio_vigencia", "fim_vigencia", "valor_mensal", "situacao")
    search_fields = ("numero", "titulo", "objeto", "fornecedor__nome", "cliente__nome", "outra_parte")
    detail_template = "juridico/contrato_detail.html"
    detail_fields = ["numero", "titulo", "tipo", "categoria", "parte", "objeto", "valor_total", "valor_mensal",
                     "indice_reajuste", "inicio_vigencia", "fim_vigencia", "data_limite_aviso", "renovacao_automatica",
                     "prazo_renovacao_meses", "aviso_previo_dias", "multa_rescisoria", "data_assinatura",
                     "assinaturas_necessarias", "responsavel", "departamento", "status", "motivo_status", "observacoes"]
    related = (("aditivos", "Aditivos e renovações", ["data", "tipo", "descricao", "nova_data_fim", "novo_valor_mensal",
                                                       "percentual_reajuste"]),)
    icon = "file-earmark-ruled"
    menu = "Jurídico"
    menu_order = 1
    can_delete = True

    @classmethod
    def after_save(cls, request, obj, created):
        from .models import Evento

        if created:
            Evento.objects.create(contrato=obj, tipo="criacao", usuario=request.user, descricao="Contrato cadastrado.")


class AditivoForm(FormBase):
    class Meta:
        model = Aditivo
        fields = ["contrato", "tipo", "data", "descricao", "nova_data_fim", "novo_valor_mensal", "percentual_reajuste"]

    def clean(self):
        dados = super().clean()
        c = dados.get("contrato")
        if c and c.status not in ("vigente", "aprovado"):
            raise forms.ValidationError("Aditivos só podem ser lançados em contratos aprovados ou vigentes.")
        return dados

    def save(self, commit=True):
        aditivo = super().save(commit=False)
        c = aditivo.contrato
        aditivo.fim_anterior, aditivo.valor_anterior = c.fim_vigencia, c.valor_mensal
        if commit:
            aditivo.save()
            if aditivo.nova_data_fim:
                c.fim_vigencia = aditivo.nova_data_fim
                c.ultimo_alerta_para = None
            if aditivo.novo_valor_mensal is not None:
                c.valor_mensal = aditivo.novo_valor_mensal
            c.save()
        return aditivo


class AditivoCrud(Crud):
    model = Aditivo
    form_class = AditivoForm
    list_display = ("data", "contrato", "tipo", "descricao", "nova_data_fim", "novo_valor_mensal")
    search_fields = ("descricao", "contrato__numero", "contrato__titulo")
    filter_fields = ("tipo",)
    icon = "file-earmark-plus"
    menu = "Jurídico"
    menu_label = "Aditivos e renovações"
    menu_order = 2
    can_edit = can_delete = False
