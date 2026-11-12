from apps.core.crud import Crud

from .models import Cobranca, HistoricoCobranca, ReguaCobranca


class CobrancaCrud(Crud):
    model = Cobranca
    fields = ["conta_receber", "tipo", "valor", "vencimento", "status", "nosso_numero", "linha_digitavel",
              "url_pagamento", "observacoes"]
    list_display = ("vencimento", "cliente", "conta_receber", "tipo", "valor", "status", "data_envio")
    search_fields = ("cliente__nome", "conta_receber__descricao", "nosso_numero")
    filter_fields = {"status": ["exact"], "tipo": ["exact"], "cliente": ["exact"], "vencimento": ["gte", "lte"]}
    detail_fields = ["conta_receber", "cliente", "tipo", "valor", "vencimento", "status", "nosso_numero",
                     "linha_digitavel", "url_pagamento", "data_envio", "observacoes", "criado_por", "criado_em"]
    detail_template = "cobranca/cobranca_detail.html"
    related = (("historico", "Histórico de envios", ["data", "destino", "assunto", "sucesso", "erro"]),)
    actions = (("cobranca:enviar_cobranca", "Enviar por e-mail", "envelope", "cobranca.change_cobranca"),)
    icon = "receipt"
    menu = "Cobrança"
    menu_order = 1


class ReguaCobrancaCrud(Crud):
    model = ReguaCobranca
    fields = ["nome", "dias", "assunto", "mensagem", "ativa"]
    list_display = ("nome", "dias", "assunto", "ativa")
    icon = "calendar2-week"
    menu = "Cobrança"
    menu_order = 2


class HistoricoCobrancaCrud(Crud):
    model = HistoricoCobranca
    list_display = ("data", "cobranca", "regra", "destino", "assunto", "sucesso")
    search_fields = ("destino", "assunto")
    filter_fields = ("sucesso", "regra")
    icon = "clock-history"
    menu = "Cobrança"
    menu_label = "Histórico de envios"
    menu_order = 3
    can_create = can_edit = can_delete = False
