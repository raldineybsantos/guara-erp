"""CNAB 240 de cobrança registrada (FEBRABAN): remessa com segmentos P e Q e leitura do retorno (segmentos T e U).

O layout segue o padrão FEBRABAN; cada banco tem particularidades (carteira, nosso número, protesto, juros) e exige
homologação do arquivo antes do uso em produção.
"""
import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.validators import somente_digitos

from .cnab import alfa, centavos, codigo_banco, num, separar_dv

OCORRENCIAS = {"02": "Entrada confirmada", "03": "Entrada rejeitada", "06": "Liquidação", "09": "Baixa",
               "17": "Liquidação após baixa", "27": "Baixa rejeitada", "28": "Débito de tarifas/custas"}


def _inscricao(doc):
    d = somente_digitos(doc or "")
    return ("1", d) if len(d) == 11 else ("2", d) if len(d) == 14 else ("0", d)


def _linha(texto):
    if len(texto) != 240:
        raise ValidationError(f"Erro interno no layout CNAB (registro com {len(texto)} posições).")
    return texto


def gerar_remessa(remessa, nsa):
    conta = remessa.conta_bancaria
    empresa = conta.empresa
    if empresa is None:
        from apps.core.models import Empresa
        empresa = Empresa.padrao()
    banco = codigo_banco(conta.codigo_banco)
    ag, ag_dv = separar_dv(conta.agencia)
    cc, cc_dv = separar_dv(conta.conta)
    insc_emp = somente_digitos(empresa.cnpj) if empresa else ""
    faltam = [rot for rot, v in (("código do banco", banco), ("agência", ag), ("conta", cc),
                                 ("convênio de cobrança", conta.convenio_cobranca), ("carteira", conta.carteira),
                                 ("empresa com CNPJ", insc_emp)) if not v]
    if faltam:
        raise ValidationError(f"Complete o cadastro da conta bancária {conta}: {', '.join(faltam)}.")
    itens = list(remessa.itens.select_related("conta_receber__cliente"))
    if not itens:
        raise ValidationError("A remessa não tem títulos.")
    erros = []
    for it in itens:
        cl = it.conta_receber.cliente
        if it.conta_receber.saldo <= 0 or it.conta_receber.status in ("pago", "cancelado", "renegociado"):
            erros.append(f"{it.conta_receber}: título já quitado, cancelado ou renegociado; retire-o da remessa.")
        elif cl is None or len(somente_digitos(cl.documento)) not in (11, 14):
            erros.append(f"{it.conta_receber}: cliente sem CPF/CNPJ válido.")
        elif not (somente_digitos(cl.cep) and cl.logradouro and cl.cidade and cl.uf):
            erros.append(f"{it.conta_receber}: endereço do cliente incompleto (CEP, logradouro, cidade e UF).")
    if erros:
        raise ValidationError(erros)

    agora = timezone.localtime()
    cab_conta = (num(ag, 5) + alfa(ag_dv, 1) + num(cc, 12) + alfa(cc_dv, 1) + " ")
    linhas = [banco + "0000" + "0" + " " * 9 + "2" + num(insc_emp, 14) + alfa(conta.convenio_cobranca, 20)
              + cab_conta + alfa(empresa.razao_social, 30) + alfa(conta.banco, 30) + " " * 10 + "1"
              + agora.strftime("%d%m%Y%H%M%S") + num(nsa, 6) + "103" + "00000" + " " * 20 + alfa(remessa.numero, 20)
              + " " * 29]
    linhas.append(banco + "0001" + "1" + "R" + "01" + "  " + "040" + " " + "2" + num(insc_emp, 14)
                  + alfa(conta.convenio_cobranca, 20) + cab_conta + alfa(empresa.razao_social, 30) + " " * 40
                  + " " * 40 + num(nsa, 8) + agora.strftime("%d%m%Y") + "0" * 8 + " " * 34)
    total = Decimal(0)
    seq = 0
    for it in itens:
        cr, cl = it.conta_receber, it.conta_receber.cliente
        seq += 1
        pre = banco + "0001" + "3"
        valor = cr.saldo
        total += valor
        linhas.append(pre + num(seq, 5) + "P" + " " + "01" + cab_conta + alfa(it.nosso_numero, 20)
                      + alfa(conta.carteira[:1], 1) + "1" + "2" + "2" + "0" + alfa(it.seu_numero, 15)
                      + cr.vencimento.strftime("%d%m%Y") + centavos(valor) + "00000" + " " + "02" + "N"
                      + cr.data_emissao.strftime("%d%m%Y") + "3" + "0" * 8 + "0" * 15 + "0" + "0" * 8 + "0" * 15
                      + "0" * 15 + "0" * 15 + alfa(cr.documento or cr.pk, 25) + "3" + "00" + "0" + "000" + "09"
                      + "0" * 10 + " ")
        seq += 1
        tipo, doc = _inscricao(cl.documento)
        linhas.append(pre + num(seq, 5) + "Q" + " " + "01" + tipo + num(doc, 15) + alfa(cl.nome, 40)
                      + alfa(f"{cl.logradouro}, {cl.numero}".strip(", "), 40) + alfa(cl.bairro, 15)
                      + num(somente_digitos(cl.cep)[:5], 5) + num(somente_digitos(cl.cep)[5:8], 3)
                      + alfa(cl.cidade, 15) + alfa(cl.uf, 2) + "0" + "0" * 15 + " " * 40 + "000" + " " * 20
                      + " " * 8)
    linhas.append(banco + "0001" + "5" + " " * 9 + num(seq + 2, 6) + num(len(itens), 6) + centavos(total, 17)
                  + "0" * 6 + "0" * 17 + "0" * 6 + "0" * 17 + "0" * 6 + "0" * 17 + " " * 8 + " " * 117)
    linhas.append(banco + "9999" + "9" + " " * 9 + num(1, 6) + num(len(linhas) + 1, 6) + "0" * 6 + " " * 205)
    return "\r\n".join(_linha(l) for l in linhas) + "\r\n"


def ler_retorno(texto):
    """[{seu_numero, nosso_numero, movimento, valor_titulo, valor_pago, data_credito}] dos pares T/U do retorno."""
    def _data(s):
        try:
            return datetime.datetime.strptime(s, "%d%m%Y").date()
        except ValueError:
            return None

    linhas = [l.ljust(240) for l in texto.replace("\r", "").split("\n") if l.strip()]
    if len(linhas) < 3 or any(len(l) != 240 for l in linhas) or linhas[0][7] != "0" or linhas[-1][7] != "9":
        raise ValidationError("O arquivo não é um retorno CNAB 240 de cobrança válido (linhas de 240 posições, "
                              "com cabeçalho e rodapé).")
    regs, atual = [], None
    for l in linhas:
        if l[7] != "3":
            continue
        if l[13] == "T":
            atual = {"movimento": l[15:17], "nosso_numero": l[37:57].strip(), "seu_numero": l[58:73].strip(),
                     "valor_titulo": Decimal(int(l[81:96] or 0)) / 100, "valor_pago": Decimal(0), "data_credito": None}
            regs.append(atual)
        elif l[13] == "U" and atual is not None:
            atual["valor_pago"] = Decimal(int(l[77:92] or 0)) / 100
            atual["data_credito"] = _data(l[145:153]) or _data(l[137:145])
            atual = None
    return regs
