from apps.core.crud import Crud

from .models import ConfiguracaoFiscal, ItemNota, NotaFiscal, Produto


class ProdutoCrud(Crud):
    model = Produto
    fields = ["codigo", "descricao", "tipo", "unidade", "ncm", "cest", "codigo_servico", "codigo_tributacao_nacional",
              "codigo_tributacao_municipal", "codigo_nbs", "cfop_padrao", "preco",
              "aliquota_icms", "aliquota_ipi", "aliquota_pis", "aliquota_cofins", "aliquota_iss", "estoque_minimo",
              "inspecionar_recebimento", "ativo"]
    list_display = ("codigo", "descricao", "tipo", "unidade", "ncm", "preco", "ativo")
    search_fields = ("codigo", "descricao", "ncm")
    filter_fields = ("tipo", "ativo")
    icon = "box-seam"
    menu = "Fiscal"
    menu_order = 2


class NotaFiscalCrud(Crud):
    model = NotaFiscal
    fields = ["empresa", "tipo", "modelo", "serie", "numero", "data_emissao", "natureza_operacao", "cliente", "fornecedor",
              "chave_acesso", "protocolo", "valor_frete", "valor_desconto", "xml", "observacoes"]
    list_display = ("data_emissao", "tipo", "modelo", "numero", "serie", "participante", "valor_total", "status",
                    "situacao_fiscal", "empresa")
    search_fields = ("numero", "chave_acesso", "cliente__nome", "fornecedor__nome")
    filter_fields = {"empresa": ["exact"], "tipo": ["exact"], "modelo": ["exact"], "status": ["exact"], "data_emissao": ["gte", "lte"]}
    inline = (ItemNota, ["produto", "descricao", "cfop", "quantidade", "valor_unitario",
                         "aliquota_icms", "aliquota_ipi", "aliquota_pis", "aliquota_cofins", "aliquota_iss"], 2)
    detail_fields = ["tipo", "modelo", "numero", "serie", "data_emissao", "natureza_operacao", "participante",
                     "chave_acesso", "protocolo", "status", "situacao_fiscal", "valor_produtos", "valor_frete", "valor_desconto",
                     "valor_icms", "valor_ipi", "valor_pis", "valor_cofins", "valor_iss", "valor_total", "link_xml",
                     "observacoes"]
    related = (("transmissoes", "Transmissões", ["data", "operacao", "emissor", "ambiente", "status", "codigo",
                                                  "mensagem", "protocolo", "usuario"]),)
    actions = (
        ("fiscal:emitir_nota", "Emitir", "send-check", "fiscal.emitir_notafiscal"),
        ("fiscal:transmitir_nota", "Transmitir ao fisco", "cloud-upload", "fiscal.emitir_notafiscal"),
        ("fiscal:cancelar_fiscal", "Cancelar no fisco", "cloud-slash", "fiscal.emitir_notafiscal"),
        ("fiscal:gerar_titulo_nota", "Gerar título financeiro", "cash-stack", "fiscal.change_notafiscal"),
        ("fiscal:cancelar_nota", "Cancelar nota", "x-octagon", "fiscal.emitir_notafiscal"),
    )
    icon = "file-earmark-text"
    menu = "Fiscal"
    menu_order = 1
    form_template = "fiscal/nota_form.html"

    @classmethod
    def after_save(cls, request, obj, created):
        obj.recalcular()


class ConfiguracaoFiscalCrud(Crud):
    model = ConfiguracaoFiscal
    fields = ["empresa", "ambiente", "emissor_nfe", "serie_nfe", "emissor_nfse", "serie_nfse",
              "usar_certificado_matriz"]
    list_display = ("empresa", "ambiente", "emissor_nfe", "emissor_nfse", "certificado_titular",
                    "certificado_validade", "status")
    detail_fields = ["empresa", "ambiente", "emissor_nfe", "emissor_nfse", "certificado_titular",
                     "certificado_validade", "status"]
    actions = (("fiscal:enviar_certificado", "Enviar certificado A1", "shield-lock",
                "fiscal.change_configuracaofiscal"),)
    can_delete = False
    icon = "cloud-upload"
    menu = "Fiscal"
    menu_label = "Configuração fiscal"
    menu_order = 8
