"""Emissor Nacional da NFS-e (Sefin Nacional / Ambiente de Dados Nacional).

Fluxo: monta a DPS (Declaração de Prestação de Serviço, leiaute 1.01), assina com o A1 da empresa (XMLDSig),
compacta em GZip + Base64 e envia por POST /nfse com TLS mútuo (o próprio certificado é a credencial). A resposta é
síncrona: a NFS-e pronta ou a lista de erros. O cancelamento é o evento 101101, por POST /nfse/{chave}/eventos.

Pontos de atenção (confira em produção restrita antes de liberar):
- uma DPS tem UM serviço: itens com o mesmo código de tributação e a mesma alíquota são somados; itens de serviços
  diferentes pedem notas separadas;
- o leiaute 1.02 (NT 009, reforma tributária) ainda é preliminar; a produção segue no 1.01;
- tudo que o fisco recusa volta com código e texto do próprio fisco, gravados na transmissão.
"""

import base64
import contextlib
import datetime
import gzip
import json
import os
import re
import stat
import tempfile
from decimal import ROUND_HALF_UP, Decimal

import requests
from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from lxml import etree

from apps.core.validators import somente_digitos

from .. import assinatura_xml
from . import Emissor, Resultado

NS = "http://www.sped.fazenda.gov.br/nfse"
VERSAO_DPS = "1.01"
VERSAO_EVENTO = "1.00"
VER_APLIC = "GuaraERP-1.0"
URLS = {"homologacao": "https://sefin.producaorestrita.nfse.gov.br/SefinNacional",
        "producao": "https://sefin.nfse.gov.br/SefinNacional"}
TP_AMB = {"homologacao": "2", "producao": "1"}
# Há versões do leiaute do evento com <nPedRegEvento>; a versão atual recusa o elemento (erro P9020 de schema).
EVENTO_COM_NPEDREG = False
D2 = Decimal("0.01")


def _dec(valor, casas=2):
    return str(Decimal(valor or 0).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP))


def _agora():
    """Instante com fuso, 1 minuto no passado: a Sefin recusa DPS 'do futuro' por diferença de relógio."""
    return timezone.localtime(timezone.now() - datetime.timedelta(minutes=1)).replace(microsecond=0)


def _el(pai, tag, texto=None, **atributos):
    e = etree.SubElement(pai, f"{{{NS}}}{tag}", **atributos)
    if texto is not None:
        e.text = str(texto)
    return e


# ------------------------------------------------------------------------------------------------ dados da nota
def _servico(nota):
    """Dados do serviço da DPS a partir dos itens, ou a lista de pendências."""
    faltas, itens = [], list(nota.itens.select_related("produto"))
    if not itens:
        return None, ["itens"]
    codigos = set()
    for i in itens:
        p = i.produto
        cod = somente_digitos(p.codigo_tributacao_nacional) if p else ""
        if len(cod) != 6:
            faltas.append(f"código de tributação nacional (6 dígitos) do serviço “{i.descricao or (p and p.descricao) or i.pk}”"
                          " em Fiscal → Produtos e serviços")
        codigos.add((cod, (i.aliquota_iss if i.aliquota_iss is not None else (p.aliquota_iss if p else 0)) or Decimal(0)))
    if len(codigos) > 1 and not faltas:
        faltas.append("itens de serviços diferentes (código de tributação ou alíquota de ISS distintos): a NFS-e "
                      "nacional tem um serviço por nota, emita uma nota para cada serviço")
    if faltas:
        return None, faltas
    (cod, aliquota), = codigos
    p = itens[0].produto
    return {"cTribNac": cod, "cTribMun": somente_digitos(p.codigo_tributacao_municipal),
            "cNBS": somente_digitos(p.codigo_nbs), "aliquota": Decimal(aliquota),
            "descricao": "; ".join((i.descricao or i.produto.descricao).strip() for i in itens)[:2000],
            "valor": sum((i.valor_total for i in itens), Decimal(0))}, []


def pendencias(nota, config):
    """Tudo que falta no cadastro para a DPS sair completa. Lista vazia = pode transmitir."""
    emp, cli, faltas = config.empresa, nota.cliente, []
    if len(somente_digitos(emp.cnpj)) != 14:
        faltas.append("CNPJ da empresa")
    if len(somente_digitos(emp.codigo_municipio)) != 7:
        faltas.append("código IBGE do município da empresa (Cadastros → Empresas)")
    if not emp.inscricao_municipal:
        faltas.append("inscrição municipal da empresa (o município precisa tê-la no Cadastro Nacional NFS-e)")
    if config.usar_certificado_matriz:
        faltas.append("certificado do próprio CNPJ: o Emissor Nacional não aceita o certificado da matriz")
    if not config.pk or config._dono_do_certificado() is None or not config._dono_do_certificado().certificado:
        faltas.append("certificado A1 da empresa (Fiscal → Configuração fiscal)")
    serie = somente_digitos(nota.serie)
    if not serie or not 1 <= int(serie) <= 49999:
        faltas.append("série entre 1 e 49999 (a faixa de aplicativos próprios do Emissor Nacional)")
    if cli is None:
        faltas.append("tomador (cliente)")
    else:
        if len(somente_digitos(cli.documento)) not in (11, 14):
            faltas.append("CPF/CNPJ do cliente")
        if not cli.nome:
            faltas.append("nome do cliente")
    _, itens_faltas = _servico(nota)
    faltas += itens_faltas
    return faltas


def _endereco_tomador(cli):
    cep, mun = somente_digitos(cli.cep), somente_digitos(cli.codigo_municipio)
    if len(cep) == 8 and len(mun) == 7 and cli.logradouro and cli.numero and cli.bairro:
        return cep, mun
    return None


# --------------------------------------------------------------------------------------------------- DPS (XML)
def id_dps(config, nota):
    cnpj = somente_digitos(config.empresa.cnpj)
    return (f"DPS{somente_digitos(config.empresa.codigo_municipio)}2{cnpj}"
            f"{int(somente_digitos(nota.serie)):05d}{int(nota.numero):015d}")


def montar_dps(nota, config):
    """DPS sem assinatura (XML em bytes) e o Id do infDPS."""
    emp, cli = config.empresa, nota.cliente
    serv, _ = _servico(nota)
    ident = id_dps(config, nota)
    raiz = etree.Element(f"{{{NS}}}DPS", nsmap={None: NS}, versao=VERSAO_DPS)
    inf = _el(raiz, "infDPS", Id=ident)
    _el(inf, "tpAmb", TP_AMB[config.ambiente])
    _el(inf, "dhEmi", _agora().isoformat())
    _el(inf, "verAplic", VER_APLIC)
    _el(inf, "serie", f"{int(somente_digitos(nota.serie)):05d}")
    _el(inf, "nDPS", int(nota.numero))
    _el(inf, "dCompet", nota.data_emissao.isoformat())
    _el(inf, "tpEmit", "1")
    _el(inf, "cLocEmi", somente_digitos(emp.codigo_municipio))

    prest = _el(inf, "prest")
    _el(prest, "CNPJ", somente_digitos(emp.cnpj))
    if emp.inscricao_municipal:
        _el(prest, "IM", somente_digitos(emp.inscricao_municipal) or emp.inscricao_municipal)
    reg = _el(prest, "regTrib")
    _el(reg, "opSimpNac", {"mei": "2", "simples": "3"}.get(emp.regime_tributario, "1"))
    if emp.regime_tributario == "simples":
        _el(reg, "regApTribSN", "1")
    _el(reg, "regEspTrib", "0")

    toma = _el(inf, "toma")
    doc = somente_digitos(cli.documento)
    _el(toma, "CNPJ" if len(doc) == 14 else "CPF", doc)
    _el(toma, "xNome", cli.nome[:150])
    end = _endereco_tomador(cli)
    if end:
        e = _el(toma, "end")
        nac = _el(e, "endNac")
        _el(nac, "cMun", end[1])
        _el(nac, "CEP", end[0])
        _el(e, "xLgr", cli.logradouro[:125])
        _el(e, "nro", cli.numero[:10])
        if cli.complemento:
            _el(e, "xCpl", cli.complemento[:60])
        _el(e, "xBairro", cli.bairro[:60])
    if cli.email:
        _el(toma, "email", cli.email[:80])

    s = _el(inf, "serv")
    _el(_el(s, "locPrest"), "cLocPrestacao", somente_digitos(emp.codigo_municipio))
    c = _el(s, "cServ")
    _el(c, "cTribNac", serv["cTribNac"])
    if serv["cTribMun"]:
        _el(c, "cTribMun", serv["cTribMun"])
    _el(c, "xDescServ", serv["descricao"])
    if serv["cNBS"]:
        _el(c, "cNBS", serv["cNBS"])

    v = _el(inf, "valores")
    _el(_el(v, "vServPrest"), "vServ", _dec(serv["valor"]))
    if nota.valor_desconto and nota.valor_desconto > 0:
        _el(_el(v, "vDescCondIncond"), "vDescIncond", _dec(nota.valor_desconto))
    trib = _el(v, "trib")
    mun = _el(trib, "tribMun")
    _el(mun, "tribISSQN", "1")
    if emp.regime_tributario not in ("simples", "mei") and serv["aliquota"] > 0:
        _el(mun, "pAliq", _dec(serv["aliquota"]))
    _el(mun, "tpRetISSQN", "1")
    fed = _el(trib, "tribFed")
    pc = _el(fed, "piscofins")
    if nota.valor_pis or nota.valor_cofins:
        itens = list(nota.itens.select_related("produto"))
        base = sum((i.valor_total for i in itens), Decimal(0)) - (nota.valor_desconto or 0)
        _el(pc, "CST", "01")
        _el(pc, "vBCPisCofins", _dec(base))
        _el(pc, "pAliqPis", _dec((itens[0].aliquota_pis or 0) if itens else 0))
        _el(pc, "pAliqCofins", _dec((itens[0].aliquota_cofins or 0) if itens else 0))
        _el(pc, "vPis", _dec(nota.valor_pis))
        _el(pc, "vCofins", _dec(nota.valor_cofins))
        _el(pc, "tpRetPisCofins", "2")
    else:
        _el(pc, "CST", "00")
    _el(_el(trib, "totTrib"), "indTotTrib", "0")
    return etree.tostring(raiz, encoding="UTF-8"), ident


def validar_xsd(xml: bytes):
    """Confere o XML com o XSD oficial quando FISCAL_NFSE_NACIONAL_XSD aponta para ele (opcional)."""
    caminho = getattr(settings, "FISCAL_NFSE_NACIONAL_XSD", "")
    if not caminho:
        return
    schema = etree.XMLSchema(etree.parse(caminho))
    doc = etree.fromstring(xml)
    if not schema.validate(doc):
        raise ValidationError("A DPS não passou no XSD oficial: " + "; ".join(e.message for e in schema.error_log)[:500])


def montar_evento_cancelamento(chave, config, justificativa):
    """Pedido de registro do evento 101101 (cancelamento), sem assinatura, e o Id do infPedReg."""
    ident = f"PRE{chave}101101" + ("001" if EVENTO_COM_NPEDREG else "")
    raiz = etree.Element(f"{{{NS}}}pedRegEvento", nsmap={None: NS}, versao=VERSAO_EVENTO)
    inf = _el(raiz, "infPedReg", Id=ident)
    _el(inf, "tpAmb", TP_AMB[config.ambiente])
    _el(inf, "verAplic", VER_APLIC)
    _el(inf, "dhEvento", _agora().isoformat())
    _el(inf, "CNPJAutor", somente_digitos(config.empresa.cnpj))
    _el(inf, "chNFSe", chave)
    if EVENTO_COM_NPEDREG:
        _el(inf, "nPedRegEvento", "001")
    ev = _el(inf, "e101101")
    _el(ev, "xDesc", "Cancelamento de NFS-e")
    _el(ev, "cMotivo", "9")
    _el(ev, "xMotivo", justificativa[:255])
    return etree.tostring(raiz, encoding="UTF-8"), ident


# ---------------------------------------------------------------------------------------------------- HTTP
def _empacotar(xml: bytes) -> str:
    return base64.b64encode(gzip.compress(xml)).decode()


def _desempacotar(b64: str) -> str:
    return gzip.decompress(base64.b64decode(b64)).decode("utf-8")


def _erros(dados):
    """Lista de erros do corpo (a Sefin responde 'erros' ou 'Erros', com 'Codigo'/'codigo')."""
    lista = []
    for chave in ("erros", "Erros", "erro", "Erro"):
        bruto = dados.get(chave) if isinstance(dados, dict) else None
        if bruto:
            lista = bruto if isinstance(bruto, list) else [bruto]
            break
    saida = []
    for e in lista:
        if not isinstance(e, dict):
            saida.append(("", str(e)))
            continue
        g = {k.lower(): v for k, v in e.items()}
        texto = g.get("descricao") or g.get("mensagem") or ""
        if g.get("complemento"):
            texto += f" ({g['complemento']})"
        saida.append((str(g.get("codigo") or ""), texto))
    return saida


def _json(resposta):
    try:
        return resposta.json()
    except ValueError:
        return {}


def _xml_do_corpo(dados):
    """Primeiro campo '...XmlGZipB64' do corpo, já descompactado."""
    for k, v in (dados or {}).items():
        if k.lower().endswith("xmlgzipb64") and v:
            try:
                return _desempacotar(v)
            except Exception:
                return ""
    return ""


@contextlib.contextmanager
def _sessao(config):
    """Sessão HTTPS com TLS mútuo. A chave privada vai para arquivos temporários (0600, em memória quando o
    sistema tem /dev/shm) só durante a chamada e é apagada em seguida."""
    chave, cert, cadeia = config.abrir_certificado(com_cadeia=True)
    base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    with tempfile.TemporaryDirectory(prefix="nfse-", dir=base) as pasta:
        pem_cert, pem_chave = os.path.join(pasta, "cert.pem"), os.path.join(pasta, "chave.pem")
        pem = cert.public_bytes(serialization.Encoding.PEM)
        for extra in cadeia:
            pem += extra.public_bytes(serialization.Encoding.PEM)
        for caminho, dados in ((pem_cert, pem), (pem_chave, chave.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))):
            fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "wb") as f:
                f.write(dados)
        s = requests.Session()
        s.cert = (pem_cert, pem_chave)
        s.verify = getattr(settings, "FISCAL_NFSE_NACIONAL_CA_BUNDLE", "") or True
        s.headers.update({"Accept": "application/json", "User-Agent": VER_APLIC})
        yield s


class NfseNacional(Emissor):
    codigo, nome = "nacional", "Emissor Nacional da NFS-e"
    homologado = True

    # ---- infraestrutura
    def _base(self, config):
        if config.ambiente == "producao" and not getattr(settings, "FISCAL_NFSE_NACIONAL_PRODUCAO", False):
            raise ValidationError(
                "A transmissão em produção pelo Emissor Nacional está travada. Emita notas de teste em "
                "homologação e, quando estiver tudo certo, defina FISCAL_NFSE_NACIONAL_PRODUCAO=1 no .env.")
        return URLS[config.ambiente]

    def _post(self, sessao, url, campo, xml):
        return sessao.post(url, data=json.dumps({campo: _empacotar(xml)}),
                           headers={"Content-Type": "application/json"},
                           timeout=getattr(settings, "FISCAL_NFSE_NACIONAL_TIMEOUT", 60))

    def _get(self, sessao, url):
        return sessao.get(url, timeout=getattr(settings, "FISCAL_NFSE_NACIONAL_TIMEOUT", 60))

    def _recuperar(self, sessao, base, ident):
        """DPS já processada (reenvio, queda no meio da resposta): devolve (chave, xml) da NFS-e existente."""
        r = self._get(sessao, f"{base}/dps/{ident}")
        if r.status_code != 200:
            return None
        chave = _json(r).get("chaveAcesso") or _json(r).get("ChaveAcesso")
        if not chave:
            return None
        r2 = self._get(sessao, f"{base}/nfse/{chave}")
        return chave, (_xml_do_corpo(_json(r2)) if r2.status_code == 200 else "")

    @staticmethod
    def _numero_nfse(xml):
        achado = re.search(r"<nNFSe>(\d+)</nNFSe>", xml or "")
        return achado.group(1) if achado else ""

    def _autorizada(self, chave, xml, enviado, mensagem):
        return Resultado(True, "100", mensagem, self._numero_nfse(xml), chave, enviado, xml)

    # ---- autorizar
    def autorizar(self, nota, config):
        base = self._base(config)
        faltas = pendencias(nota, config)
        if faltas:
            return Resultado(False, "GUARA-001", "Falta no cadastro: " + "; ".join(faltas[:6]) + ".")
        chave_priv, cert = config.abrir_certificado()
        xml, ident = montar_dps(nota, config)
        validar_xsd(xml)
        enviado = assinatura_xml.assinar(xml, ident, chave_priv, cert).decode()
        with _sessao(config) as s:
            try:
                r = self._post(s, f"{base}/nfse", "dpsXmlGZipB64", enviado.encode())
            except requests.RequestException as e:
                try:
                    achado = self._recuperar(s, base, ident)
                except requests.RequestException:
                    achado = None
                if achado:
                    return self._autorizada(*achado, enviado, "NFS-e já processada pelo Emissor Nacional "
                                                              "(recuperada após falha de comunicação).")
                return Resultado(False, "COM", f"Sem resposta do Emissor Nacional: {e.__class__.__name__}. A nota "
                                                "não foi decidida; tente de novo.", xml_enviado=enviado, erro=True)
            dados = _json(r)
            if r.status_code in (200, 201):
                xml_nfse = _xml_do_corpo(dados)
                chave = dados.get("chaveAcesso") or dados.get("ChaveAcesso") or ""
                return self._autorizada(chave, xml_nfse, enviado, "NFS-e autorizada pelo Emissor Nacional.")
            erros = _erros(dados)
            if any(c == "E0014" for c, _ in erros):   # DPS duplicada: a nota já existe, só falta trazê-la
                try:
                    achado = self._recuperar(s, base, ident)
                except requests.RequestException:
                    achado = None
                if achado:
                    return self._autorizada(*achado, enviado, "NFS-e já existia no Emissor Nacional (DPS repetida).")
            if r.status_code in (500, 502, 503, 504):
                return Resultado(False, f"HTTP{r.status_code}", "O Emissor Nacional falhou ao processar a DPS; "
                                 "tente de novo mais tarde.", xml_enviado=enviado, xml_retorno=r.text[:4000], erro=True)
            if r.status_code == 403:
                return Resultado(False, "HTTP403", "Certificado recusado pelo Emissor Nacional (inválido, vencido, "
                                 "fora da cadeia ICP-Brasil ou de outro CNPJ).", xml_enviado=enviado,
                                 xml_retorno=r.text[:4000])
            texto = "; ".join(f"{c}: {m}" if c else m for c, m in erros)[:480] or f"HTTP {r.status_code}"
            return Resultado(False, (erros[0][0] if erros else f"HTTP{r.status_code}"), "Rejeição: " + texto,
                             xml_enviado=enviado, xml_retorno=r.text[:4000])

    # ---- cancelar
    def cancelar(self, nota, config, justificativa):
        base = self._base(config)
        chave = somente_digitos(nota.chave_acesso)
        if len(chave) != 50:
            return Resultado(False, "GUARA-002", "A nota não tem a chave de acesso de 50 dígitos da NFS-e nacional.")
        chave_priv, cert = config.abrir_certificado()
        xml, ident = montar_evento_cancelamento(chave, config, justificativa)
        enviado = assinatura_xml.assinar(xml, ident, chave_priv, cert).decode()
        with _sessao(config) as s:
            try:
                r = self._post(s, f"{base}/nfse/{chave}/eventos", "pedidoRegistroEventoXmlGZipB64", enviado.encode())
            except requests.RequestException as e:
                return Resultado(False, "COM", f"Sem resposta do Emissor Nacional: {e.__class__.__name__}. O "
                                                "cancelamento não foi decidido; confira a nota e tente de novo.",
                                 xml_enviado=enviado, erro=True)
            dados = _json(r)
            if r.status_code in (200, 201):
                return Resultado(True, "101101", "Cancelamento registrado no Emissor Nacional.",
                                 str(dados.get("protocolo") or ""), chave, enviado, _xml_do_corpo(dados))
            erros = _erros(dados)
            texto = "; ".join(f"{c}: {m}" if c else m for c, m in erros)[:480] or f"HTTP {r.status_code}"
            return Resultado(False, (erros[0][0] if erros else f"HTTP{r.status_code}"), "Rejeição: " + texto,
                             xml_enviado=enviado, xml_retorno=r.text[:4000],
                             erro=r.status_code in (500, 502, 503, 504))


# ------------------------------------------------------------------------------------------- diagnóstico
def diagnosticar(config, conectar=False):
    """Confere se a empresa está pronta para emitir pelo Emissor Nacional. Devolve [(nível, texto)], com nível
    "ok", "aviso" ou "erro". Com `conectar`, faz uma consulta que não emite nada, só para provar o TLS mútuo."""
    from ..models import Produto

    emp, linhas = config.empresa, []

    def add(nivel, texto):
        linhas.append((nivel, texto))

    add("ok" if config.emissor_nfse == "nacional" else "aviso",
        f"NFS-e vai por: {config.get_emissor_nfse_display()}")
    add("ok", f"Ambiente: {config.get_ambiente_display()} ({URLS[config.ambiente]})")
    if config.ambiente == "producao" and not getattr(settings, "FISCAL_NFSE_NACIONAL_PRODUCAO", False):
        add("aviso", "Produção travada: defina FISCAL_NFSE_NACIONAL_PRODUCAO=1 no .env depois dos testes")
    status = config.status
    add("ok" if status == "Vigente" else ("aviso" if status.startswith("Atenção") else "erro"),
        f"Certificado A1: {status}" + (f" ({config.certificado_titular})" if config.certificado_titular else ""))
    if config.usar_certificado_matriz:
        add("erro", "A filial usa o certificado da matriz; o Emissor Nacional exige o do próprio CNPJ")
    for ok, texto in ((len(somente_digitos(emp.cnpj)) == 14, "CNPJ"),
                      (len(somente_digitos(emp.codigo_municipio)) == 7, "código IBGE do município"),
                      (bool(emp.inscricao_municipal), "inscrição municipal"),
                      (bool(emp.regime_tributario), "regime tributário")):
        add("ok" if ok else "erro", f"Empresa: {texto} {'informado' if ok else 'FALTA'}")
    serie = somente_digitos(config.serie_nfse)
    add("ok" if serie and 1 <= int(serie) <= 49999 else "erro", f"Série da NFS-e: {config.serie_nfse or '—'} "
        "(use de 1 a 49999)")
    sem = Produto.objects.filter(tipo="servico", ativo=True).exclude(codigo_tributacao_nacional__regex=r"^\d{6}$")
    add("ok" if not sem.exists() else "aviso",
        "Serviços cadastrados com código de tributação nacional" if not sem.exists()
        else f"{sem.count()} serviço(s) sem código de tributação nacional (ex.: {sem.first().descricao})")
    if conectar:
        try:
            with _sessao(config) as s:
                r = s.get(f"{URLS[config.ambiente]}/dps/DPS{'0' * 42}", timeout=30)
            if r.status_code in (200, 404, 400):
                add("ok", f"Conexão com TLS mútuo funcionou (a Sefin respondeu HTTP {r.status_code})")
            elif r.status_code == 403:
                add("erro", "A Sefin recusou o certificado (HTTP 403): confira validade, cadeia ICP-Brasil e CNPJ")
            else:
                add("aviso", f"A Sefin respondeu HTTP {r.status_code}")
        except ValidationError as e:
            add("erro", " ".join(e.messages))
        except requests.RequestException as e:
            add("erro", f"Sem conexão com a Sefin: {e.__class__.__name__} ({str(e)[:120]})")
    return linhas
