"""Simulação interna: percorre o fluxo completo (XML, assinatura com o A1, protocolo, cancelamento) sem enviar
nada a ninguém. Serve para treinar a equipe e validar cadastros. Não tem valor fiscal."""

from django.utils import timezone
from lxml import etree

from apps.core.validators import somente_digitos

from .. import assinatura_xml, chave
from . import Emissor, Resultado


class Simulacao(Emissor):
    codigo, nome, homologado = "simulacao", "Simulação interna", True

    def _xml(self, nota, config, chave_acesso):
        ident = f"SIM{chave_acesso or nota.pk}"
        raiz = etree.Element("documentoSimulado")
        inf = etree.SubElement(raiz, "inf", Id=ident, semValorFiscal="true")
        emp, dest = config.empresa, nota.cliente

        def campo(pai, tag, valor):
            etree.SubElement(pai, tag).text = str(valor if valor is not None else "")

        campo(inf, "modelo", nota.modelo)
        campo(inf, "numero", nota.numero)
        campo(inf, "serie", nota.serie)
        campo(inf, "emissao", nota.data_emissao.isoformat())
        campo(inf, "natureza", nota.natureza_operacao)
        e = etree.SubElement(inf, "emitente")
        campo(e, "cnpj", somente_digitos(emp.cnpj))
        campo(e, "nome", emp.razao_social)
        d = etree.SubElement(inf, "destinatario")
        campo(d, "documento", somente_digitos(dest.documento) if dest else "")
        campo(d, "nome", dest.nome if dest else "")
        itens = etree.SubElement(inf, "itens")
        for i in nota.itens.all():
            it = etree.SubElement(itens, "item")
            campo(it, "descricao", i.descricao)
            campo(it, "ncm", i.ncm)
            campo(it, "cfop", i.cfop)
            campo(it, "quantidade", i.quantidade)
            campo(it, "valorUnitario", i.valor_unitario)
            campo(it, "valorTotal", i.valor_total)
        t = etree.SubElement(inf, "totais")
        for nome in ("produtos", "icms", "ipi", "pis", "cofins", "iss", "total"):
            campo(t, nome, getattr(nota, f"valor_{nome}"))
        chave_priv, cert = config.abrir_certificado()
        return assinatura_xml.assinar(etree.tostring(raiz), ident, chave_priv, cert).decode()

    def autorizar(self, nota, config):
        faltas = []
        if nota.cliente is None:
            faltas.append("destinatário (cliente)")
        elif not somente_digitos(nota.cliente.documento):
            faltas.append("CPF/CNPJ do cliente")
        if nota.modelo in ("55", "65"):
            if not config.empresa.uf:
                faltas.append("UF da empresa")
            faltas += [f"NCM de “{i.descricao}”" for i in nota.itens.all() if not i.ncm]
            faltas += [f"CFOP de “{i.descricao}”" for i in nota.itens.all() if not i.cfop]
        if faltas:
            return Resultado(False, "SIM-225", "Rejeição: falta " + "; ".join(faltas[:6]) + ".")
        ch = ""
        if nota.modelo in ("55", "65"):
            ch = chave.gerar(config.empresa.uf, nota.data_emissao, config.empresa.cnpj, nota.modelo, nota.serie,
                             nota.numero, codigo=nota.pk % 10 ** 8)
        xml = self._xml(nota, config, ch)
        return Resultado(True, "SIM-100", "Autorizado o uso (SIMULAÇÃO, sem valor fiscal).",
                         f"SIM{timezone.now():%Y%m%d%H%M%S}{nota.pk:04d}"[:30], ch, xml)

    def cancelar(self, nota, config, justificativa):
        return Resultado(True, "SIM-135", "Cancelamento registrado (SIMULAÇÃO, sem valor fiscal).",
                         f"SIMC{timezone.now():%Y%m%d%H%M%S}"[:30])
