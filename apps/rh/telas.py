from django import forms

from apps.core.crud import Crud, FormBase

from .desempenho import AcaoDesenvolvimento, Avaliacao, CicloAvaliacao, Competencia, ItemModelo, ModeloAvaliacao
from .salarios import Enquadramento, FaixaSalarial, MovimentoSalarial
from .recrutamento import Candidato, Candidatura, Entrevista, Vaga
from .treinamentos import Curso, Participacao, Turma
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
    acao_status = {
        "rh:ferias_aprovar": ('solicitada',),
        "rh:ferias_recusar": ('solicitada',),
        "rh:ferias_gozada": ('aprovada',),
    }
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
    acao_status = {
        "rh:folha_importar_ponto": ('aberta', 'calculada'),
        "rh:folha_calcular": ('aberta', 'calculada'),
        "rh:folha_fechar": ('calculada',),
        "rh:folha_reabrir": ('fechada',),
        "rh:esocial_da_folha": ('calculada', 'fechada'),
    }
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
    acao_status = {
        "rh:rescisao_calcular": ('calculada',),
        "rh:esocial_da_rescisao": ('efetivada',),
        "rh:rescisao_efetivar": ('calculada',),
    }
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
    acao_status = {
        "rh:esocial_assinar": ('gerado',),
        "rh:esocial_lote": ('assinado',),
        "rh:esocial_recibo": ('enviado',),
    }
    actions = (("rh:esocial_assinar", "Assinar", "pen", "rh.change_eventoesocial"),
               ("rh:esocial_lote", "Baixar como lote", "box-arrow-down", "rh.view_eventoesocial"),
               ("rh:esocial_baixar", "Baixar XML", "download", "rh.view_eventoesocial"),
               ("rh:esocial_recibo", "Registrar retorno do governo", "receipt", "rh.change_eventoesocial"))
    icon = "cloud-arrow-up"
    menu = "RH"
    menu_label = "Eventos do eSocial"
    menu_order = 60


# ------------------------------------------------------------------ treinamentos
class CursoCrud(Crud):
    model = Curso
    list_display = ("nome", "categoria", "modalidade", "carga_horaria", "validade_meses", "ativo")
    search_fields = ("nome", "descricao")
    filter_fields = ("categoria", "modalidade", "ativo")
    detail_fields = ["nome", "categoria", "modalidade", "carga_horaria", "validade_meses", "frequencia_minima",
                     "nota_minima", "cargos_obrigatorios", "descricao", "ativo"]
    related = (("turmas", "Turmas", ["numero", "data_inicio", "data_fim", "status"]),)
    icon = "mortarboard"
    menu = "RH"
    menu_label = "Cursos"
    menu_order = 70


class TurmaCrud(Crud):
    model = Turma
    fields = ["empresa", "curso", "instrutor", "fornecedor", "local", "data_inicio", "data_fim", "vagas", "custo_total",
              "observacoes"]
    list_display = ("numero", "curso", "data_inicio", "data_fim", "vagas", "status")
    search_fields = ("numero", "curso__nome", "instrutor", "fornecedor")
    filter_fields = ("status", "curso")
    detail_fields = ["numero", "curso", "instrutor", "fornecedor", "local", "data_inicio", "data_fim", "vagas",
                     "custo_total", "status", "observacoes", "criado_por", "criado_em"]
    related = (("participacoes", "Participantes",
                ["colaborador", "presenca_pct", "nota", "resultado", "valido_ate"]),)
    acao_status = {
        "rh:turma_inscrever": ('planejada', 'andamento'),
        "rh:turma_iniciar": ('planejada',),
        "rh:turma_resultados": ('andamento',),
        "rh:turma_concluir": ('andamento',),
        "rh:turma_cancelar": ('planejada', 'andamento'),
    }
    actions = (
        ("rh:turma_inscrever", "Inscrever colaboradores", "person-plus", "rh.gerir_treinamento"),
        ("rh:turma_iniciar", "Iniciar turma", "play-circle", "rh.gerir_treinamento"),
        ("rh:turma_resultados", "Lançar frequência e nota", "ui-checks", "rh.gerir_treinamento"),
        ("rh:turma_concluir", "Concluir turma", "check2-circle", "rh.gerir_treinamento"),
        ("rh:turma_cancelar", "Cancelar turma", "x-circle", "rh.gerir_treinamento"),
    )
    can_delete = False
    icon = "easel"
    menu = "RH"
    menu_label = "Turmas de treinamento"
    menu_order = 71


class ParticipacaoCrud(Crud):
    model = Participacao
    empresa_campo = "turma__empresa"
    list_display = ("colaborador", "turma", "presenca_pct", "nota", "resultado", "valido_ate")
    search_fields = ("colaborador__nome", "turma__numero", "turma__curso__nome")
    filter_fields = ("resultado", "turma__curso")
    detail_fields = ["colaborador", "turma", "presenca_pct", "nota", "resultado", "concluido_em", "valido_ate"]
    can_create = False
    can_edit = False
    can_delete = False
    icon = "award"
    menu = "RH"
    menu_label = "Histórico de treinamentos"
    menu_order = 72


# ------------------------------------------------------------------ avaliação de desempenho
class CompetenciaCrud(Crud):
    model = Competencia
    fields = ["nome", "descricao", "ativo"]
    list_display = ("nome", "ativo")
    search_fields = ("nome",)
    icon = "stars"
    menu = "RH"
    menu_label = "Competências"
    menu_order = 80


class ModeloAvaliacaoCrud(Crud):
    model = ModeloAvaliacao
    fields = ["nome", "escala_max", "descricao", "ativo"]
    list_display = ("nome", "escala_max", "ativo")
    search_fields = ("nome",)
    related = (("itens", "Competências e pesos", ["competencia", "peso"]),)
    icon = "ui-checks-grid"
    menu = "RH"
    menu_label = "Modelos de avaliação"
    menu_order = 81


class ItemModeloCrud(Crud):
    model = ItemModelo
    fields = ["modelo", "competencia", "peso"]
    list_display = ("modelo", "competencia", "peso")
    search_fields = ("modelo__nome", "competencia__nome")
    icon = "list-check"
    menu = "RH"
    menu_label = "Itens do modelo"
    menu_order = 82


class CicloAvaliacaoCrud(Crud):
    model = CicloAvaliacao
    fields = ["empresa", "nome", "modelo", "data_inicio", "data_fim", "inclui_auto", "peso_gestor"]
    list_display = ("nome", "modelo", "data_inicio", "data_fim", "status")
    search_fields = ("nome",)
    filter_fields = ("status",)
    detail_fields = ["nome", "modelo", "data_inicio", "data_fim", "inclui_auto", "peso_gestor", "status"]
    related = (("avaliacoes", "Avaliações",
                ["colaborador", "gestor", "status", "nota_auto", "nota_gestor", "nota_final", "nota_calibrada"]),)
    acao_status = {
        "rh:ciclo_abrir": ('rascunho',),
        "rh:ciclo_calibrar": ('aberto', 'calibracao'),
        "rh:ciclo_encerrar": ('aberto', 'calibracao'),
    }
    actions = (
        ("rh:ciclo_abrir", "Abrir ciclo", "play-circle", "rh.gerir_avaliacao"),
        ("rh:ciclo_calibrar", "Calibrar notas", "sliders", "rh.gerir_avaliacao"),
        ("rh:ciclo_encerrar", "Encerrar ciclo", "check2-circle", "rh.gerir_avaliacao"),
    )
    can_delete = False
    icon = "clipboard2-data"
    menu = "RH"
    menu_label = "Ciclos de avaliação"
    menu_order = 83


class AvaliacaoCrud(Crud):
    model = Avaliacao
    empresa_campo = "ciclo__empresa"
    list_display = ("ciclo", "colaborador", "gestor", "status", "nota_final", "nota_calibrada")
    search_fields = ("colaborador__nome", "ciclo__nome")
    filter_fields = ("status", "ciclo")
    detail_fields = ["ciclo", "colaborador", "gestor", "status", "nota_auto", "nota_gestor", "nota_final",
                     "nota_calibrada", "calibracao_motivo", "comentario_auto", "comentario_gestor"]
    related = (("respostas", "Notas por competência", ["item", "tipo", "nota", "comentario"]),
               ("acoes", "PDI", ["descricao", "competencia", "curso", "prazo", "status"]))
    acao_status = {
        "rh:avaliacao_responder_auto": ('pendente', 'andamento'),
        "rh:avaliacao_responder_gestor": ('pendente', 'andamento'),
        "rh:avaliacao_pdi": ('concluida',),
    }
    actions = (
        ("rh:avaliacao_responder_auto", "Autoavaliação", "person-check", None),
        ("rh:avaliacao_responder_gestor", "Avaliar como gestor", "person-lines-fill", None),
        ("rh:avaliacao_pdi", "Adicionar ação ao PDI", "plus-circle", "rh.gerir_avaliacao"),
    )
    can_create = False
    can_edit = False
    can_delete = False
    icon = "clipboard2-check"
    menu = "RH"
    menu_label = "Avaliações"
    menu_order = 84


class AcaoDesenvolvimentoCrud(Crud):
    model = AcaoDesenvolvimento
    empresa_campo = "avaliacao__ciclo__empresa"
    list_display = ("avaliacao", "descricao", "competencia", "prazo", "status")
    search_fields = ("descricao", "avaliacao__colaborador__nome")
    filter_fields = ("status",)
    acao_status = {
        "rh:pdi_concluir": ('aberta',),
        "rh:pdi_cancelar": ('aberta',),
    }
    actions = (
        ("rh:pdi_concluir", "Concluir ação", "check2", "rh.gerir_avaliacao"),
        ("rh:pdi_cancelar", "Cancelar ação", "x-circle", "rh.gerir_avaliacao"),
    )
    can_create = False
    can_delete = False
    icon = "signpost-split"
    menu = "RH"
    menu_label = "PDI"
    menu_order = 85


# ------------------------------------------------------------------ recrutamento e seleção
class VagaCrud(Crud):
    model = Vaga
    fields = ["empresa", "titulo", "cargo", "departamento", "gestor", "tipo_contrato", "quantidade", "faixa_min",
              "faixa_max", "descricao"]
    list_display = ("numero", "titulo", "cargo", "quantidade", "status")
    search_fields = ("numero", "titulo", "cargo__nome")
    filter_fields = ("status", "cargo")
    detail_fields = ["numero", "titulo", "cargo", "departamento", "gestor", "tipo_contrato", "quantidade", "faixa_min",
                     "faixa_max", "status", "aberta_em", "fechada_em", "descricao"]
    related = (("candidaturas", "Candidaturas", ["candidato", "etapa", "proposta_status"]),)
    acao_status = {
        "rh:vaga_abrir": ('rascunho', 'pausada'),
        "rh:vaga_pausar": ('aberta',),
        "rh:vaga_cancelar": ('rascunho', 'aberta', 'pausada'),
        "rh:vaga_funil": ('aberta', 'pausada', 'preenchida'),
    }
    actions = (
        ("rh:vaga_abrir", "Abrir / reabrir vaga", "megaphone", "rh.gerir_recrutamento"),
        ("rh:vaga_pausar", "Pausar vaga", "pause-circle", "rh.gerir_recrutamento"),
        ("rh:vaga_cancelar", "Cancelar vaga", "x-circle", "rh.gerir_recrutamento"),
        ("rh:vaga_funil", "Ver funil", "funnel", "rh.view_vaga"),
    )
    can_delete = False
    icon = "person-plus"
    menu = "RH"
    menu_label = "Vagas"
    menu_order = 90


class CandidatoCrud(Crud):
    model = Candidato
    fields = ["nome", "cpf", "email", "telefone", "origem", "pretensao", "curriculo", "consentimento_lgpd",
              "banco_talentos"]
    list_display = ("nome", "email", "telefone", "origem", "banco_talentos")
    search_fields = ("nome", "email", "telefone")
    filter_fields = ("origem", "banco_talentos")
    related = (("candidaturas", "Candidaturas", ["vaga", "etapa"]),)
    icon = "person-vcard"
    menu = "RH"
    menu_label = "Candidatos"
    menu_order = 91


class CandidaturaCrud(Crud):
    model = Candidatura
    empresa_campo = "vaga__empresa"
    fields = ["vaga", "candidato"]
    list_display = ("candidato", "vaga", "etapa", "proposta_status")
    search_fields = ("candidato__nome", "vaga__titulo", "vaga__numero")
    filter_fields = ("etapa", "vaga")
    detail_fields = ["vaga", "candidato", "etapa", "motivo", "proposta_salario", "proposta_inicio", "proposta_status",
                     "colaborador"]
    related = (("historico", "Histórico do funil", ["em", "de", "para", "observacao", "usuario"]),
               ("entrevistas", "Entrevistas", ["tipo", "data_hora", "entrevistador", "nota", "parecer"]))
    actions = (
        ("rh:candidatura_mover", "Mover de etapa", "arrow-right-circle", "rh.gerir_recrutamento"),
        ("rh:candidatura_proposta", "Enviar proposta", "envelope-paper", "rh.gerir_recrutamento"),
        ("rh:candidatura_aceite", "Registrar aceite", "hand-thumbs-up", "rh.gerir_recrutamento"),
        ("rh:candidatura_recusa", "Registrar recusa", "hand-thumbs-down", "rh.gerir_recrutamento"),
        ("rh:candidatura_contratar", "Contratar", "person-check", "rh.gerir_recrutamento"),
        ("rh:candidatura_reprovar", "Reprovar", "x-octagon", "rh.gerir_recrutamento"),
        ("rh:candidatura_desistir", "Registrar desistência", "box-arrow-left", "rh.gerir_recrutamento"),
    )
    can_edit = False
    can_delete = False
    icon = "diagram-3"
    menu = "RH"
    menu_label = "Candidaturas"
    menu_order = 92


class EntrevistaCrud(Crud):
    model = Entrevista
    empresa_campo = "candidatura__vaga__empresa"
    fields = ["candidatura", "tipo", "data_hora", "entrevistador", "nota", "parecer"]
    list_display = ("candidatura", "tipo", "data_hora", "nota")
    search_fields = ("candidatura__candidato__nome",)
    filter_fields = ("tipo",)
    can_delete = False
    icon = "chat-square-text"
    menu = "RH"
    menu_label = "Entrevistas"
    menu_order = 93


# ------------------------------------------------------------------ plano de cargos e salários
class FaixaSalarialCrud(Crud):
    model = FaixaSalarial
    fields = ["cargo", "nivel", "minimo", "maximo", "ativo"]
    list_display = ("cargo", "nivel", "minimo", "maximo", "ativo")
    search_fields = ("cargo__nome",)
    filter_fields = ("nivel", "ativo")
    related = (("enquadrados", "Colaboradores na faixa", ["colaborador"]),)
    can_delete = False
    icon = "bar-chart-steps"
    menu = "RH"
    menu_label = "Faixas salariais"
    menu_order = 100


class EnquadramentoCrud(Crud):
    model = Enquadramento
    empresa_campo = "colaborador__empresa"
    fields = ["colaborador", "faixa"]
    list_display = ("colaborador", "faixa")
    search_fields = ("colaborador__nome", "faixa__cargo__nome")
    icon = "diagram-2"
    menu = "RH"
    menu_label = "Enquadramentos"
    menu_order = 101


class MovimentoSalarialCrud(Crud):
    model = MovimentoSalarial
    empresa_campo = "colaborador__empresa"
    fields = ["colaborador", "tipo", "data_efetiva", "salario_novo", "cargo_novo", "faixa_nova", "excecao", "motivo"]
    list_display = ("colaborador", "tipo", "data_efetiva", "salario_novo", "status")
    search_fields = ("colaborador__nome", "motivo", "lote")
    filter_fields = ("status", "tipo")
    detail_fields = ["colaborador", "tipo", "data_efetiva", "salario_anterior", "salario_novo", "cargo_anterior",
                     "cargo_novo", "faixa_nova", "excecao", "motivo", "lote", "status", "decidido_por", "decidido_em",
                     "parecer", "aplicado_em", "criado_por"]
    acao_status = {
        "rh:movimento_aprovar": ('pendente',),
        "rh:movimento_rejeitar": ('pendente',),
        "rh:movimento_cancelar": ('pendente', 'aprovado'),
    }
    actions = (
        ("rh:movimento_aprovar", "Aprovar", "check2-circle", "rh.aprovar_salarios"),
        ("rh:movimento_rejeitar", "Rejeitar", "x-circle", "rh.aprovar_salarios"),
        ("rh:movimento_cancelar", "Cancelar", "slash-circle", "rh.gerir_salarios"),
    )
    can_edit = False
    can_delete = False
    icon = "graph-up-arrow"
    menu = "RH"
    menu_label = "Movimentações salariais"
    menu_order = 102
