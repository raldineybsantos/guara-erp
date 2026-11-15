from django import forms

from apps.core.crud import Crud, FormBase

from .models import (AbonoPonto, AjusteBancoHoras, Beneficio, BeneficioColaborador, DadosTrabalhistas, Dependente,
                     ConfiguracaoESocial, EventoESocial, FaixaINSS, FaixaIRRF, Feriado, Folha, Jornada, LancamentoFolha, Marcacao,
                     ParametroFolha, PeriodoAquisitivo, Rescisao, Rubrica, SolicitacaoFerias)


# ------------------------------------------------------------------ tabelas legais
class ParametroFolhaCrud(Crud):
    model = ParametroFolha
    list_display = ("vigencia_inicio", "descricao", "salario_minimo", "familia_cota", "dia_pagamento")
    detail_fields = ["vigencia_inicio", "descricao", "salario_minimo", "deducao_dependente", "desconto_simplificado",
                     "reducao_ate", "reducao_maxima", "reducao_limite", "reducao_fixo", "reducao_fator",
                     "familia_limite", "familia_cota", "fgts_aliquota", "fgts_aprendiz", "patronal_aliquota", "rat",
                     "terceiros", "prolabore_inss", "dia_pagamento", "dia_guias"]
    inline = (FaixaINSS, ["ate", "aliquota"], 5)
    related = (("faixas_irrf_set", "Faixas do IRRF (cadastre em 'Faixas do IRRF')", ["ate", "aliquota", "deduzir"]),)
    icon = "percent"
    menu = "RH"
    menu_label = "Tabelas legais (INSS, IRRF)"
    menu_order = 90


class FaixaIRRFCrud(Crud):
    model = FaixaIRRF
    empresa_campo = "nada"
    list_display = ("parametro", "ate", "aliquota", "deduzir")
    icon = "percent"
    menu = "RH"
    menu_label = "Faixas do IRRF"
    menu_order = 91


# ------------------------------------------------------------------ cadastros
class JornadaCrud(Crud):
    model = Jornada
    list_display = ("nome", "horas_dia", "dias_semana", "banco_horas", "ativo")
    icon = "clock"
    menu = "RH"
    menu_order = 10


class DadosTrabalhistasCrud(Crud):
    model = DadosTrabalhistas
    empresa_campo = "colaborador__empresa"
    list_display = ("colaborador", "jornada", "pis", "categoria_esocial")
    search_fields = ("colaborador__nome", "pis")
    icon = "person-vcard"
    menu = "RH"
    menu_order = 11


class DependenteCrud(Crud):
    model = Dependente
    empresa_campo = "colaborador__empresa"
    list_display = ("nome", "colaborador", "parentesco", "data_nascimento", "deduz_irrf")
    search_fields = ("nome", "colaborador__nome")
    filter_fields = ("parentesco", "deduz_irrf")
    icon = "people"
    menu = "RH"
    menu_order = 12


class BeneficioCrud(Crud):
    model = Beneficio
    empresa_campo = "nada"
    list_display = ("nome", "tipo", "valor_mensal", "desconto_percentual", "desconto_fixo", "ativo")
    icon = "gift"
    menu = "RH"
    menu_order = 13


class BeneficioColaboradorCrud(Crud):
    model = BeneficioColaborador
    empresa_campo = "colaborador__empresa"
    list_display = ("colaborador", "beneficio", "valor", "inicio", "fim")
    search_fields = ("colaborador__nome",)
    icon = "gift"
    menu = "RH"
    menu_label = "Benefícios dos colaboradores"
    menu_order = 14


class RubricaCrud(Crud):
    model = Rubrica
    empresa_campo = "nada"
    list_display = ("codigo", "nome", "tipo", "incide_inss", "incide_irrf", "incide_fgts", "natureza_esocial")
    filter_fields = ("tipo",)
    icon = "list-columns"
    menu = "RH"
    menu_order = 15


# ------------------------------------------------------------------ ponto
class _ColabLimitado(Crud):
    """Quem não gerencia o ponto vê só os próprios registros."""

    perm_gestao = "rh.gerir_ponto"
    empresa_campo = "colaborador__empresa"

    @classmethod
    def get_queryset(cls, request):
        qs = super().get_queryset(request)
        if request.user.has_perm(cls.perm_gestao):
            return qs
        return qs.filter(colaborador__usuario=request.user)


class MarcacaoForm(FormBase):
    class Meta:
        model = Marcacao
        fields = ["colaborador", "momento", "justificativa"]


class MarcacaoCrud(_ColabLimitado):
    model = Marcacao
    form_class = MarcacaoForm
    list_display = ("colaborador", "momento", "origem", "justificativa")
    search_fields = ("colaborador__nome",)
    filter_fields = {"origem": ["exact"], "momento": ["gte", "lte"]}
    can_edit = False
    can_delete = False   # o ponto é prova: correções entram como novo lançamento justificado
    icon = "fingerprint"
    menu = "RH"
    menu_label = "Marcações de ponto"
    menu_order = 20

    @classmethod
    def after_save(cls, request, obj, created):
        if created:
            Marcacao.objects.filter(pk=obj.pk).update(origem="manual")


class AbonoPontoCrud(_ColabLimitado):
    model = AbonoPonto
    list_display = ("colaborador", "data", "tipo", "observacao")
    search_fields = ("colaborador__nome",)
    filter_fields = ("tipo",)
    icon = "calendar-check"
    menu = "RH"
    menu_order = 21


class AjusteBancoHorasCrud(_ColabLimitado):
    model = AjusteBancoHoras
    list_display = ("colaborador", "data", "minutos", "motivo")
    search_fields = ("colaborador__nome",)
    can_delete = False
    icon = "hourglass-split"
    menu = "RH"
    menu_label = "Ajustes do banco de horas"
    menu_order = 22


class FeriadoCrud(Crud):
    model = Feriado
    empresa_campo = "nada"
    list_display = ("data", "nome")
    icon = "calendar-event"
    menu = "RH"
    menu_order = 23


# ------------------------------------------------------------------ férias
class PeriodoAquisitivoCrud(_ColabLimitado):
    model = PeriodoAquisitivo
    perm_gestao = "rh.gerir_ferias"
    list_display = ("colaborador", "inicio", "fim", "dias_direito", "saldo", "situacao_rotulo")
    search_fields = ("colaborador__nome",)
    detail_fields = ["colaborador", "inicio", "fim", "dias_direito", "dias_usados", "saldo", "limite_concessivo",
                     "situacao_rotulo"]
    related = (("solicitacoes", "Solicitações de férias", ["inicio", "dias", "abono_dias", "status"]),)
    actions = (("rh:gerar_periodos", "Gerar períodos de todos", "arrow-repeat", "rh.add_periodoaquisitivo"),)
    icon = "umbrella"
    menu = "RH"
    menu_label = "Períodos aquisitivos"
    menu_order = 30


class SolicitacaoFeriasForm(FormBase):
    class Meta:
        model = SolicitacaoFerias
        fields = ["colaborador", "periodo", "inicio", "dias", "abono_dias", "adiantar_13"]

    def clean(self):
        from . import ferias

        d = super().clean()
        if all(d.get(k) for k in ("colaborador", "periodo", "inicio", "dias")):
            s = SolicitacaoFerias(pk=self.instance.pk, colaborador=d["colaborador"], periodo=d["periodo"],
                                  inicio=d["inicio"], dias=d["dias"], abono_dias=d.get("abono_dias") or 0)
            try:
                ferias.validar(s)
            except forms.ValidationError as e:
                raise forms.ValidationError(e.messages) from None
        return d


class SolicitacaoFeriasCrud(_ColabLimitado):
    model = SolicitacaoFerias
    perm_gestao = "rh.gerir_ferias"
    form_class = SolicitacaoFeriasForm
    list_display = ("colaborador", "inicio", "dias", "abono_dias", "status")
    search_fields = ("colaborador__nome",)
    filter_fields = ("status",)
    can_delete = False
    actions = (("rh:ferias_aprovar", "Aprovar", "check2-circle", None),
               ("rh:ferias_recusar", "Recusar", "x-circle", None),
               ("rh:ferias_gozada", "Marcar como gozada", "check2-all", "rh.aprovar_solicitacaoferias"))
    icon = "airplane"
    menu = "RH"
    menu_label = "Solicitações de férias"
    menu_order = 31


# ------------------------------------------------------------------ folha
class LancamentoFolhaCrud(Crud):
    model = LancamentoFolha
    empresa_campo = "colaborador__empresa"
    fields = ["colaborador", "competencia", "rubrica", "valor", "referencia"]
    list_display = ("competencia", "colaborador", "rubrica", "valor", "referencia", "origem")
    search_fields = ("colaborador__nome", "rubrica__nome")
    filter_fields = {"competencia": ["exact"], "origem": ["exact"]}
    icon = "plus-slash-minus"
    menu = "RH"
    menu_label = "Lançamentos da folha"
    menu_order = 40


class FolhaCrud(Crud):
    model = Folha
    fields = ["empresa", "competencia", "tipo", "observacoes"]
    list_display = ("competencia", "tipo", "empresa", "total_bruto", "total_liquido", "status")
    filter_fields = ("tipo", "status")
    detail_template = "rh/folha_detalhe.html"
    detail_fields = ["empresa", "competencia", "tipo", "status", "total_bruto", "total_descontos", "total_liquido",
                     "total_inss", "total_irrf", "total_fgts", "total_patronal", "fechada_em", "fechada_por",
                     "observacoes"]
    actions = (("rh:folha_importar_ponto", "Importar ponto (extras e faltas)", "fingerprint", "rh.fechar_folha"),
               ("rh:folha_calcular", "Calcular", "calculator", "rh.fechar_folha"),
               ("rh:folha_fechar", "Fechar e gerar contas a pagar", "lock", "rh.fechar_folha"),
               ("rh:folha_reabrir", "Reabrir", "unlock", "rh.fechar_folha"),
               ("rh:esocial_da_folha", "Gerar eventos eSocial (S-1200/S-1210)", "cloud-arrow-up",
                "rh.add_eventoesocial"))
    can_delete = False
    icon = "cash-coin"
    menu = "RH"
    menu_label = "Folhas de pagamento"
    menu_order = 41

    @classmethod
    def extra_detalhe(cls, request, obj):
        return {"holerites": obj.holerites.select_related("colaborador")}


class RescisaoCrud(Crud):
    model = Rescisao
    fields = ["empresa", "colaborador", "data_desligamento", "motivo", "aviso", "saldo_fgts"]
    empresa_campo = "empresa"
    list_display = ("colaborador", "data_desligamento", "motivo", "total_liquido", "status")
    filter_fields = ("motivo", "status")
    detail_template = "rh/rescisao_detalhe.html"
    detail_fields = ["colaborador", "data_desligamento", "motivo", "aviso", "saldo_fgts", "total_proventos",
                     "total_descontos", "total_liquido", "multa_fgts", "status"]
    actions = (("rh:rescisao_calcular", "Calcular", "calculator", "rh.change_rescisao"),
               ("rh:esocial_da_rescisao", "Gerar evento eSocial S-2299", "cloud-arrow-up", "rh.add_eventoesocial"),
               ("rh:rescisao_efetivar", "Efetivar (desligar e gerar conta)", "person-x", "rh.efetivar_rescisao"))
    can_delete = False
    icon = "person-dash"
    menu = "RH"
    menu_label = "Rescisões"
    menu_order = 42


# ------------------------------------------------------------------ eSocial
class ConfiguracaoESocialCrud(Crud):
    model = ConfiguracaoESocial
    list_display = ("empresa", "contato_nome", "contato_email", "porte_me_epp")
    icon = "gear"
    menu = "RH"
    menu_label = "Configuração do eSocial"
    menu_order = 59


class EventoESocialCrud(Crud):
    model = EventoESocial
    list_display = ("criado_em", "tipo", "colaborador", "competencia", "status", "recibo")
    search_fields = ("colaborador__nome", "identificador", "recibo")
    filter_fields = ("tipo", "status")
    detail_fields = ["tipo", "empresa", "colaborador", "competencia", "status", "identificador", "recibo", "retorno"]
    detail_template = "rh/evento_detalhe.html"
    can_create = False
    can_delete = False
    fields = ["recibo", "retorno"]
    actions = (("rh:esocial_assinar", "Assinar", "pen", "rh.change_eventoesocial"),
               ("rh:esocial_lote", "Baixar como lote", "box-arrow-down", "rh.view_eventoesocial"),
               ("rh:esocial_baixar", "Baixar XML", "download", "rh.view_eventoesocial"),
               ("rh:esocial_recibo", "Registrar retorno do governo", "receipt", "rh.change_eventoesocial"))
    icon = "cloud-arrow-up"
    menu = "RH"
    menu_label = "Eventos do eSocial"
    menu_order = 60
