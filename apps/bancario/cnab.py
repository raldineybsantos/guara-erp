"""CNAB 240 (padrão FEBRABAN) para pagamento a fornecedores: remessa e leitura do retorno.

Lote de créditos em conta (segmento A, TED) e lote de títulos/boletos (segmento J). O layout segue o padrão
FEBRABAN; cada banco tem particularidades e exige homologação do arquivo antes do uso em produção.
"""

import unicodedata
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.validators import somente_digitos

OCORRENCIAS = {"00": "Pagamento efetuado", "BD": "Agendado pelo banco", "AB": "Tipo de operação inválido",
               "AG": "Agência/conta do pagador inválida", "AL": "Banco do favorecido inválido",
               "AM": "Agência do favorecido inválida", "AN": "Conta do favorecido inválida",
               "AP": "Data de pagamento inválida", "AR": "Valor inválido", "CC": "Código de barras inválido",
               "SA": "Saldo insuficiente", "ZA": "Agência/conta do favorecido encerrada"}


def num(valor, tamanho):
    d = somente_digitos(str(valor if valor is not None else ""))
    if len(d) > tamanho:
        raise ValidationError(f"Valor {valor!r} não cabe em {tamanho} dígitos no arquivo.")
    return d.rjust(tamanho, "0")


def alfa(valor, tamanho):
    t = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().upper()
    t = "".join(c if 32 <= ord(c) < 127 else " " for c in t)
    return t[:tamanho].ljust(tamanho)


def centavos(valor, tamanho=15):
    return num(int((Decimal(valor) * 100).quantize(Decimal("1"))), tamanho)


def separar_dv(texto):
    """'1234-5' -> ('1234', '5'); '12345' -> ('12345', ' ')."""
    texto = (texto or "").strip().upper()
    if "-" in texto:
        corpo, dv = texto.rsplit("-", 1)
        return somente_digitos(corpo), (dv[:1] or " ")
    return somente_digitos(texto), " "


def codigo_banco(texto):
    d = somente_digitos((texto or "")[:6])
    return d[:3] if len(d) >= 3 else ""


def linha_para_barras(codigo):
    """Aceita o código de barras (44 dígitos) ou a linha digitável de boleto (47) e devolve os 44."""
    d = somente_digitos(codigo)
    if len(d) == 44:
        return d
    if len(d) == 47:
        return d[0:4] + d[32] + d[33:47] + d[4:9] + d[10:20] + d[21:31]
    raise ValidationError("Código de barras deve ter 44 dígitos (ou 47 na linha digitável do boleto). "
                          "Contas de consumo/tributos (48 dígitos) não entram nesta remessa.")


def _pagador(conta):
    empresa = conta.empresa
    if empresa is None:
        from apps.core.models import Empresa
        empresa = Empresa.padrao()
    banco = codigo_banco(conta.codigo_banco)
    ag, ag_dv = separar_dv(conta.agencia)
    cc, cc_dv = separar_dv(conta.conta)
    faltam = [rot for rot, v in (("código do banco", banco), ("agência", ag), ("conta", cc),
                                 ("empresa com CNPJ", empresa and somente_digitos(empresa.cnpj))) if not v]
    if faltam:
        raise ValidationError(f"Complete o cadastro da conta bancária {conta}: {', '.join(faltam)}.")
    cab = ("2" + num(empresa.cnpj, 14) + alfa(conta.convenio, 20) + num(ag, 5) + alfa(ag_dv, 1) + num(cc, 12)
           + alfa(cc_dv, 1) + " " + alfa(empresa.razao_social, 30))
    return banco, cab, empresa


def gerar_remessa(bordero, nsa):
    """Texto do arquivo de remessa. Levanta ValidationError com tudo o que falta nos cadastros."""
    conta = bordero.conta_bancaria
    banco, cab, empresa = _pagador(conta)
    agora = timezone.localtime()
    data_pg = bordero.data_pagamento.strftime("%d%m%Y")
    itens = list(bordero.itens.select_related("conta_pagar__fornecedor"))
    if not itens:
        raise ValidationError("O borderô não tem títulos.")
    erros, creditos, titulos = [], [], []
    for it in itens:
        cp, f = it.conta_pagar, it.conta_pagar.fornecedor
        try:
            if it.forma == "titulo":
                titulos.append((it, linha_para_barras(cp.codigo_barras)))
            else:
                if f is None:
                    raise ValidationError("sem fornecedor")
                fb = codigo_banco(f.banco)
                ag, ag_dv = separar_dv(f.agencia)
                cc, cc_dv = separar_dv(f.conta)
                if not (fb and ag and cc and somente_digitos(f.documento)):
                    raise ValidationError(
                        f"fornecedor {f} sem CPF/CNPJ ou dados bancários (o campo Banco deve começar pelo código "
                        "de 3 dígitos, ex.: 341 Itaú)")
                creditos.append((it, fb, ag, ag_dv, cc, cc_dv))
        except ValidationError as e:
            erros.append(f"{cp}: {' '.join(e.messages)}")
    if erros:
        raise ValidationError(erros)

    linhas = [banco + "0000" + "0" + " " * 9 + cab + alfa(conta.banco, 30) + " " * 10 + "1"
              + agora.strftime("%d%m%Y%H%M%S") + num(nsa, 6) + "103" + "00000" + " " * 20 + alfa(bordero.numero, 20)
              + " " * 29]
    n_lote = 0

    def lote(forma, layout, registros):
        nonlocal n_lote
        n_lote += 1
        pre = banco + num(n_lote, 4)
        linhas.append(pre + "1" + "C" + "20" + forma + layout + " " + cab + " " * 40 + " " * 30 + "0" * 5 + " " * 15
                      + " " * 20 + "0" * 5 + " " * 3 + " " * 2 + "01" + " " * 6 + " " * 10)
        total = Decimal(0)
        for seq, (valor, corpo) in enumerate(registros, start=1):
            linhas.append(pre + "3" + num(seq, 5) + corpo)
            total += valor
        linhas.append(pre + "5" + " " * 9 + num(len(registros) + 2, 6) + centavos(total, 18) + "0" * 18 + "0" * 6
                      + " " * 165 + " " * 10)

    if creditos:
        regs = []
        for it, fb, ag, ag_dv, cc, cc_dv in creditos:
            f = it.conta_pagar.fornecedor
            regs.append((it.valor, "A" + "0" + "00" + "018" + fb + num(ag, 5) + alfa(ag_dv, 1) + num(cc, 12)
                         + alfa(cc_dv, 1) + " " + alfa(f.nome, 30) + alfa(it.seu_numero, 20) + data_pg + "BRL"
                         + "0" * 15 + centavos(it.valor) + " " * 20 + "0" * 8 + "0" * 15 + alfa(it.conta_pagar, 40)
                         + "  " + " " * 5 + "  " + " " * 3 + "0" + " " * 10))
        lote("41", "046", regs)
    if titulos:
        regs = []
        for it, barras in titulos:
            cp = it.conta_pagar
            regs.append((it.valor, "J" + "0" + "00" + barras + alfa(cp.fornecedor or cp.descricao, 30)
                         + cp.vencimento.strftime("%d%m%Y") + centavos(cp.valor) + centavos(cp.desconto)
                         + centavos(cp.juros) + data_pg + centavos(it.valor) + "0" * 15 + alfa(it.seu_numero, 20)
                         + " " * 20 + "09" + " " * 6 + " " * 10))
        lote("31", "040", regs)
    linhas.append(banco + "9999" + "9" + " " * 9 + num(n_lote, 6) + num(len(linhas) + 1, 6) + "0" * 6 + " " * 205)
    ruins = [i for i, l in enumerate(linhas, start=1) if len(l) != 240]
    if ruins:  # defesa: nunca entregar arquivo fora do layout
        raise ValidationError(f"Erro interno no layout CNAB (linhas {ruins}).")
    return "\r\n".join(linhas) + "\r\n"


def ler_retorno(texto):
    """[{seu_numero, ocorrencia, valor, data}] dos segmentos A e J de um retorno CNAB 240."""
    import datetime

    def _data(s):
        try:
            return datetime.datetime.strptime(s, "%d%m%Y").date()
        except ValueError:
            return None

    linhas = [l.ljust(240) for l in texto.replace("\r", "").split("\n") if l.strip()]
    if len(linhas) < 2 or any(len(l) != 240 for l in linhas) or linhas[0][7] != "0" or linhas[-1][7] != "9":
        raise ValidationError("O arquivo não é um retorno CNAB 240 válido (linhas de 240 posições, com cabeçalho "
                              "e rodapé).")
    registros = []
    for l in linhas:
        if l[7] != "3":
            continue
        seg = l[13]
        if seg == "A":
            registros.append({"seu_numero": l[73:93].strip(), "ocorrencia": l[230:232].strip() or "00",
                              "valor": Decimal(int(l[162:177] or 0)) / 100 or Decimal(int(l[119:134])) / 100,
                              "data": _data(l[154:162]) or _data(l[93:101])})
        elif seg == "J":
            registros.append({"seu_numero": l[182:202].strip(), "ocorrencia": l[230:232].strip() or "00",
                              "valor": Decimal(int(l[152:167])) / 100, "data": _data(l[144:152])})
    return registros
