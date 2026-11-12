"""Importação de XML de NF-e (modelo 55) recebida de fornecedores."""
import datetime
from decimal import Decimal

import defusedxml.ElementTree as ET  # protege contra XXE e "bomba de entidades"
from django.db import transaction

from apps.core.models import Fornecedor
from apps.core.validators import somente_digitos

from .models import ItemNota, NotaFiscal

NS = {"n": "http://www.portalfiscal.inf.br/nfe"}


def _t(no, caminho, padrao=""):
    el = no.find(caminho, NS) if no is not None else None
    return el.text.strip() if el is not None and el.text else padrao


def _dec(no, caminho):
    return Decimal(_t(no, caminho, "0") or "0")


@transaction.atomic
def importar_nfe(arquivo, empresa=None):
    """Lê o XML, cadastra o fornecedor se não existir e cria a nota de entrada com seus itens."""
    from apps.core.validators import validar_xml

    validar_xml(arquivo)
    conteudo = arquivo.read()
    raiz = ET.fromstring(conteudo)
    inf = raiz.find(".//n:infNFe", NS)
    if inf is None:
        raise ValueError("Arquivo não parece ser um XML de NF-e (infNFe não encontrado).")
    chave = (inf.get("Id") or "").replace("NFe", "")
    if chave and NotaFiscal.objects.filter(chave_acesso=chave, tipo="entrada").exists():
        raise ValueError(f"NF-e {chave} já foi importada.")

    ide, emit, total = inf.find("n:ide", NS), inf.find("n:emit", NS), inf.find("n:total/n:ICMSTot", NS)
    cnpj = somente_digitos(_t(emit, "n:CNPJ") or _t(emit, "n:CPF"))
    fornecedor = Fornecedor.objects.filter(documento__in=[cnpj]).first()
    if not fornecedor:
        end = emit.find("n:enderEmit", NS)
        fornecedor = Fornecedor.objects.create(
            tipo_pessoa="PJ" if len(cnpj) == 14 else "PF", documento=cnpj,
            nome=_t(emit, "n:xNome")[:200], nome_fantasia=_t(emit, "n:xFant")[:200],
            inscricao_estadual=_t(emit, "n:IE"), logradouro=_t(end, "n:xLgr"), numero=_t(end, "n:nro"),
            bairro=_t(end, "n:xBairro"), cidade=_t(end, "n:xMun"), uf=_t(end, "n:UF"), cep=_t(end, "n:CEP"),
            telefone=_t(end, "n:fone"),
        )

    data = (_t(ide, "n:dhEmi") or _t(ide, "n:dEmi"))[:10]
    nota = NotaFiscal(
        empresa=empresa, tipo="entrada", modelo=_t(ide, "n:mod", "55"), numero=int(_t(ide, "n:nNF", "0") or 0),
        serie=_t(ide, "n:serie", "1"), data_emissao=datetime.date.fromisoformat(data) if data else None,
        natureza_operacao=_t(ide, "n:natOp")[:100], fornecedor=fornecedor, chave_acesso=chave,
        protocolo=_t(raiz, ".//n:protNFe/n:infProt/n:nProt"), status="emitida",
        valor_frete=_dec(total, "n:vFrete"), valor_desconto=_dec(total, "n:vDesc"),
    )
    nota.save()
    for det in inf.findall("n:det", NS):
        prod, imp = det.find("n:prod", NS), det.find("n:imposto", NS)

        def aliq(grupo):
            el = imp.find(f".//n:{grupo}", NS) if imp is not None else None
            return _dec(el, ".//n:p" + grupo) if el is not None else Decimal(0)

        ItemNota.objects.create(
            nota=nota, descricao=_t(prod, "n:xProd")[:200], cfop=_t(prod, "n:CFOP"), ncm=_t(prod, "n:NCM"),
            quantidade=_dec(prod, "n:qCom"), valor_unitario=_dec(prod, "n:vUnCom"),
            aliquota_icms=aliq("ICMS"), aliquota_ipi=aliq("IPI"), aliquota_pis=aliq("PIS"),
            aliquota_cofins=aliq("COFINS"), aliquota_iss=Decimal(0),
        )
    nota.recalcular()
    arquivo.seek(0)
    nota.xml.save(f"{chave or nota.pk}.xml", arquivo, save=True)
    return nota
