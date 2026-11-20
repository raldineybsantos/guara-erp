import datetime

import django_filters
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.utils import timezone

from apps.core.crud import Crud, FormBase
from apps.core.validators import validar_arquivo_seguro
from apps.seguranca.antivirus import validador as validar_antivirus

from .models import Aditivo, Caucao, Contrato, Medicao, Paralisacao, ValorIndice
from .processos import Prazo, Processo

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
                              validators=[validar_arquivo_seguro, validar_antivirus],
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
                                                       "percentual_reajuste"]),
               ("medicoes", "Medições", ["numero", "periodo_inicio", "periodo_fim", "valor", "multas", "bonificacoes", "valor_liquido", "status"]),
               ("caucoes", "Garantias / cauções", ["tipo", "valor", "validade", "emissor", "status"]),
               ("paralisacoes", "Paralisações", ["inicio", "fim", "dias", "motivo"]))
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


class ValorIndiceCrud(Crud):
    model = ValorIndice
    list_display = ("indice", "competencia", "variacao")
    filter_fields = ("indice",)
    icon = "graph-up-arrow"
    menu = "Jurídico"
    menu_label = "Índices (IPCA, IGP-M, INPC)"
    menu_order = 5
    can_delete = True


class MedicaoForm(FormBase):
    class Meta:
        model = Medicao
        fields = ["contrato", "periodo_inicio", "periodo_fim", "descricao", "valor", "multas", "bonificacoes",
                  "vencimento", "observacoes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contrato"].queryset = Contrato.objects.filter(status="vigente")
        if self.instance.pk:
            self.fields["contrato"].disabled = True


class MedicaoCrud(Crud):
    model = Medicao
    form_class = MedicaoForm
    list_display = ("contrato", "numero", "periodo_inicio", "periodo_fim", "valor", "multas", "bonificacoes", "valor_liquido", "status")
    search_fields = ("contrato__numero", "contrato__titulo", "descricao")
    filter_fields = ("status", "contrato")
    detail_fields = ["contrato", "numero", "periodo_inicio", "periodo_fim", "descricao", "valor", "multas", "bonificacoes",
                     "valor_liquido", "vencimento", "status", "data_aprovacao", "aprovada_por", "contapagar", "contareceber",
                     "observacoes"]
    acao_status = {
        "juridico:medicao_aprovar": ('rascunho',),
        "juridico:medicao_faturar": ('aprovada',),
        "juridico:medicao_cancelar": ('rascunho', 'aprovada'),
    }
    actions = (
        ("juridico:medicao_aprovar", "Aprovar medição", "check2-circle", "juridico.aprovar_contrato"),
        ("juridico:medicao_faturar", "Gerar título (a pagar / a receber)", "receipt", "juridico.change_contrato"),
        ("juridico:medicao_cancelar", "Cancelar", "x-circle", "juridico.change_contrato"),
    )
    icon = "rulers"
    menu = "Jurídico"
    menu_label = "Medições de contratos"
    menu_order = 3
    can_delete = False


class CaucaoCrud(Crud):
    model = Caucao
    list_display = ("contrato", "tipo", "valor", "emissor", "validade", "status")
    search_fields = ("contrato__numero", "emissor", "apolice")
    filter_fields = ("status", "tipo")
    acao_status = {
        "juridico:caucao_devolver": ('ativa',),
        "juridico:caucao_executar": ('ativa',),
    }
    actions = (
        ("juridico:caucao_devolver", "Devolver / liberar", "arrow-return-left", "juridico.change_contrato"),
        ("juridico:caucao_executar", "Executar a garantia", "exclamation-octagon", "juridico.change_contrato"),
    )
    icon = "shield-check"
    menu = "Jurídico"
    menu_label = "Cauções e garantias"
    menu_order = 4
    can_delete = True


class ParalisacaoForm(FormBase):
    class Meta:
        model = Paralisacao
        fields = ["contrato", "inicio", "motivo", "estende_prazo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contrato"].queryset = Contrato.objects.filter(status="vigente")


class ParalisacaoCrud(Crud):
    model = Paralisacao
    form_class = ParalisacaoForm
    list_display = ("contrato", "inicio", "fim", "dias", "estende_prazo")
    search_fields = ("contrato__numero", "motivo")
    filter_fields = ("estende_prazo",)
    actions = (("juridico:paralisacao_retomar", "Retomar contrato", "play-circle", "juridico.change_contrato"),)
    icon = "pause-circle"
    menu = "Jurídico"
    menu_label = "Paralisações"
    menu_order = 6
    can_delete = False


class ProcessoCrud(Crud):
    model = Processo
    fields = ["empresa", "numero", "tipo", "natureza", "assunto", "orgao", "comarca", "polo", "contraparte",
              "documento_contraparte", "advogado", "responsavel", "contrato", "data_distribuicao", "valor_causa",
              "observacoes"]
    list_display = ("numero", "assunto", "contraparte", "natureza", "risco", "provisao", "status")
    search_fields = ("numero", "assunto", "contraparte", "advogado")
    filter_fields = ("status", "natureza", "risco", "polo", "tipo")
    detail_fields = ["numero", "tipo", "natureza", "assunto", "orgao", "comarca", "polo", "contraparte",
                     "documento_contraparte", "advogado", "responsavel", "contrato", "data_distribuicao",
                     "valor_causa", "risco", "valor_risco", "provisao", "status", "resultado", "data_encerramento",
                     "valor_condenacao", "observacoes", "criado_por", "criado_em"]
    related = (
        ("prazos", "Prazos", ["descricao", "data_fatal", "status", "responsavel"]),
        ("andamentos", "Andamentos", ["data", "tipo", "descricao"]),
        ("envolvidos", "Envolvidos", ["nome", "papel", "documento", "contato"]),
        ("reavaliacoes", "Histórico de risco e provisão", ["data", "risco", "valor_estimado", "ajuste", "motivo"]),
    )
    acao_status = {
        "juridico:processo_andamento": ('andamento', 'suspenso'),
        "juridico:processo_prazo": ('andamento', 'suspenso'),
        "juridico:processo_envolvido": ('andamento', 'suspenso'),
        "juridico:processo_risco": ('andamento', 'suspenso'),
        "juridico:processo_suspender": ('andamento',),
        "juridico:processo_retomar": ('suspenso',),
        "juridico:processo_encerrar": ('andamento', 'suspenso'),
    }
    actions = (
        ("juridico:processo_andamento", "Registrar andamento", "journal-plus", "juridico.add_andamento"),
        ("juridico:processo_prazo", "Novo prazo", "alarm", "juridico.add_prazo"),
        ("juridico:processo_envolvido", "Adicionar envolvido", "person-plus", "juridico.add_envolvido"),
        ("juridico:processo_risco", "Reavaliar risco e provisão", "speedometer", "juridico.change_processo"),
        ("juridico:processo_suspender", "Suspender", "pause-circle", "juridico.change_processo"),
        ("juridico:processo_retomar", "Retomar", "play-circle", "juridico.change_processo"),
        ("juridico:processo_encerrar", "Encerrar processo", "check2-circle", "juridico.change_processo"),
    )
    can_delete = False
    icon = "bank"
    menu = "Jurídico"
    menu_label = "Processos"
    menu_order = 7


class PrazoCrud(Crud):
    model = Prazo
    list_display = ("data_fatal", "descricao", "processo", "responsavel", "status")
    search_fields = ("descricao", "processo__numero", "processo__contraparte")
    filter_fields = ("status",)
    detail_fields = ["processo", "descricao", "data_fatal", "alertar_dias", "responsavel", "status", "observacoes"]
    acao_status = {
        "juridico:prazo_cumprir": ('pendente',),
        "juridico:prazo_cancelar": ('pendente',),
        "juridico:prazo_perdido": ('pendente',),
    }
    actions = (("juridico:prazo_cumprir", "Marcar como cumprido", "check-lg", "juridico.change_prazo"),
               ("juridico:prazo_cancelar", "Cancelar prazo", "x-lg", "juridico.change_prazo"),
               ("juridico:prazo_perdido", "Registrar como perdido", "exclamation-octagon", "juridico.change_prazo"))
    can_create = False      # nasce em Processos → Novo prazo
    can_edit = False
    can_delete = False
    icon = "alarm"
    menu = "Jurídico"
    menu_label = "Prazos processuais"
    menu_order = 8
