from apps.core.crud import Crud

from .models import ItemNota, NotaFiscal, Produto


class ProdutoCrud(Crud):
    model = Produto
    fields = ["codigo", "descricao", "tipo", "unidade", "ncm", "cest", "codigo_servico", "cfop_padrao", "preco",
              "aliquota_icms", "aliquota_ipi", "aliquota_pis", "aliquota_cofins", "aliquota_iss", "ativo"]
    list_display = ("codigo", "descricao", "tipo", "unidade", "ncm", "preco", "ativo")
    search_fields = ("codigo", "descricao", "ncm")
    filter_fields = ("tipo", "ativo")
    icon = "box-seam"
    menu = "Fiscal"
    menu_order = 2


class NotaFiscalCrud(Crud):
    model = NotaFiscal
    fields = ["tipo", "modelo", "serie", "numero", "data_emissao", "natureza_operacao", "cliente", "fornecedor",
              "chave_acesso", "protocolo", "valor_frete", "valor_desconto", "xml", "observacoes"]
    list_display = ("data_emissao", "tipo", "modelo", "numero", "serie", "participante", "valor_total", "status")
    search_fields = ("numero", "chave_acesso", "cliente__nome", "fornecedor__nome")
    filter_fields = {"tipo": ["exact"], "modelo": ["exact"], "status": ["exact"], "data_emissao": ["gte", "lte"]}
    inline = (ItemNota, ["produto", "descricao", "cfop", "quantidade", "valor_unitario",
                         "aliquota_icms", "aliquota_ipi", "aliquota_pis", "aliquota_cofins", "aliquota_iss"], 2)
    detail_fields = ["tipo", "modelo", "numero", "serie", "data_emissao", "natureza_operacao", "participante",
                     "chave_acesso", "protocolo", "status", "valor_produtos", "valor_frete", "valor_desconto",
                     "valor_icms", "valor_ipi", "valor_pis", "valor_cofins", "valor_iss", "valor_total", "xml",
                     "observacoes"]
    actions = (
        ("fiscal:emitir_nota", "Emitir", "send-check", "fiscal.emitir_notafiscal"),
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
