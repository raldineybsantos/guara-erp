"""Geração e assinatura dos eventos do eSocial (leiaute S-1.3).

ATENÇÃO — estado desta integração: os XMLs seguem a estrutura do leiaute S-1.3, mas NÃO foram validados contra
os esquemas XSD oficiais nem enviados ao ambiente de produção restrita (falta o certificado A1 da empresa).
O envio ao governo não é feito pelo sistema: baixe o lote assinado e transmita pelo portal/ferramenta oficial,
ou valide aqui primeiro quando tiver o certificado. Veja docs/MANUAL.md (seção RH → eSocial)."""
import datetime

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.utils import timezone
from lxml import etree

from apps.core.validators import somente_digitos
from apps.fiscal import assinatura_xml

from .models import ConfiguracaoESocial, EventoESocial, Rubrica

VERSAO = "S_01_03_00"
NS_LOTE = "http://www.esocial.gov.br/schema/lote/eventos/envio/v1_1_1"
VER_PROC = "Guara-ERP"
GRUPO_LOTE = {"S-1000": 1, "S-1010": 1, "S-2200": 2, "S-2299": 2, "S-1200": 3, "S-1210": 3, "S-1299": 3}
EVT = {"S-1000": "evtInfoEmpregador", "S-1010": "evtTabRubrica", "S-2200": "evtAdmissao", "S-2299": "evtDeslig",
       "S-1200": "evtRemun", "S-1210": "evtPgtos", "S-1299": "evtFechaEvPer"}
MOTIVO_DESLIGAMENTO = {"sem_justa_causa": "02", "justa_causa": "01", "pedido": "07", "acordo": "33",
                       "fim_contrato": "06"}
CODIGOS_SEM_RUBRICA = {"9001", "9002", "8001"}    # INSS, IRRF e FGTS são apurados pelo próprio eSocial
_seq = {"n": 0}


# ------------------------------------------------------------------ utilidades
def _ns(tipo):
    return f"http://www.esocial.gov.br/schema/evt/{EVT[tipo]}/v_{VERSAO}"


def _agora():
    return timezone.now()


def _id(empresa):
    _seq["n"] = (_seq["n"] + 1) % 100000
    return f"ID1{somente_digitos(empresa.cnpj).ljust(14, '0')[:14]}{_agora():%Y%m%d%H%M%S}{_seq['n']:05d}"


def _sub(pai, tag, texto=None, **attrs):
    el = etree.SubElement(pai, f"{{{pai.nsmap[None]}}}{tag}", **attrs)
    if texto is not None:
        el.text = str(texto)
    return el


def _tp_amb(empresa):
    try:
        return "1" if empresa.config_fiscal.ambiente == "producao" else "2"
    except ObjectDoesNotExist:
        return "2"


def _base(tipo, empresa, periodico=False, per_apur=None, ind_apuracao="1"):
    ns = _ns(tipo)
    raiz = etree.Element(f"{{{ns}}}eSocial", nsmap={None: ns})
    ident = _id(empresa)
    evt = _sub(raiz, EVT[tipo], Id=ident)
    ide = _sub(evt, "ideEvento")
    _sub(ide, "indRetif", 1)
    if periodico:
        _sub(ide, "indApuracao", ind_apuracao)
        _sub(ide, "perApur", per_apur)
    _sub(ide, "tpAmb", _tp_amb(empresa))
    _sub(ide, "procEmi", 1)
    _sub(ide, "verProc", VER_PROC)
    emp = _sub(evt, "ideEmpregador")
    _sub(emp, "tpInsc", 1)
    _sub(emp, "nrInsc", somente_digitos(empresa.cnpj)[:8])
    return raiz, evt, ident


def _xml(raiz):
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8").decode()


def _config(empresa):
    try:
        return empresa.config_esocial
    except ObjectDoesNotExist:
        raise ValidationError(f"Preencha a configuração do eSocial de {empresa} (RH → Configuração do eSocial): "
                              "contato (nome e CPF) do empregador.") from None


def _salvar(tipo, empresa, xml, ident, colaborador=None, competencia=None):
    return EventoESocial.objects.create(tipo=tipo, empresa=empresa, colaborador=colaborador, competencia=competencia,
                                        xml=xml, identificador=ident)


def _pendencias(msgs):
    if msgs:
        raise ValidationError(["Faltam dados para o eSocial:"] + msgs)


def _mes(data):
    return f"{data:%Y-%m}"


# ------------------------------------------------------------------ eventos
def gerar_s1000(empresa):
    cfg = _config(empresa)
    pend = []
    if not somente_digitos(empresa.cnpj):
        pend.append("CNPJ da empresa.")
    _pendencias(pend)
    raiz, evt, ident = _base("S-1000", empresa)
    info = _sub(evt, "infoEmpregador")
    inc = _sub(info, "inclusao")
    per = _sub(inc, "idePeriodo")
    _sub(per, "iniValid", _mes(empresa.criado_em.date() if empresa.criado_em else timezone.localdate()))
    cad = _sub(inc, "infoCadastro")
    _sub(cad, "classTrib", "01" if empresa.regime_tributario in ("simples", "mei") else "99")
    _sub(cad, "indCoop", 0)
    _sub(cad, "indConstr", 0)
    _sub(cad, "indDesFolha", 0)
    _sub(cad, "indPorte", "S" if cfg.porte_me_epp else "N")
    _sub(cad, "indOptRegEletron", 0)
    ctt = _sub(cad, "contato")
    _sub(ctt, "nmCtt", cfg.contato_nome)
    _sub(ctt, "cpfCtt", somente_digitos(cfg.contato_cpf))
    if cfg.contato_telefone:
        _sub(ctt, "foneFixo", somente_digitos(cfg.contato_telefone))
    if cfg.contato_email:
        _sub(ctt, "email", cfg.contato_email)
    return _salvar("S-1000", empresa, _xml(raiz), ident)


def gerar_s1010(empresa, rubricas=None):
    """Um evento por rubrica (tabela de rubricas)."""
    rubricas = list(rubricas or Rubrica.objects.all())
    pend = [f"Natureza eSocial (Tabela 3) da rubrica {r}." for r in rubricas if not r.natureza_esocial]
    _pendencias(pend)
    eventos = []
    for r in rubricas:
        raiz, evt, ident = _base("S-1010", empresa)
        info = _sub(evt, "infoRubrica")
        inc = _sub(info, "inclusao")
        ide = _sub(inc, "ideRubrica")
        _sub(ide, "codRubr", r.codigo)
        _sub(ide, "ideTabRubr", "TAB1")
        _sub(ide, "iniValid", _mes(r.criado_em.date()))
        dados = _sub(inc, "dadosRubrica")
        _sub(dados, "dscRubr", r.nome[:100])
        _sub(dados, "natRubr", r.natureza_esocial)
        _sub(dados, "tpRubr", {"provento": 1, "desconto": 2, "informativo": 3}[r.tipo])
        _sub(dados, "codIncCP", "11" if r.incide_inss else "00")
        _sub(dados, "codIncIRRF", "11" if r.incide_irrf else "00")
        _sub(dados, "codIncFGTS", "11" if r.incide_fgts else "00")
        eventos.append(_salvar("S-1010", empresa, _xml(raiz), ident))
    return eventos


def _pendencias_colaborador(c):
    pend = []
    try:
        d = c.dados_trabalhistas
    except ObjectDoesNotExist:
        return [f"{c}: preencha os dados trabalhistas."]
    for rotulo, ok in (("PIS/NIS", d.pis), ("sexo", d.sexo), ("estado civil", d.estado_civil),
                       ("grau de instrução", d.grau_instrucao), ("CBO", d.cbo), ("data de nascimento", c.data_nascimento),
                       ("CEP", c.cep), ("logradouro", c.logradouro), ("bairro", c.bairro), ("UF", c.uf),
                       ("município de residência (IBGE)", d.codigo_municipio), ("matrícula", c.matricula)):
        if not ok:
            pend.append(f"{c}: {rotulo}.")
    return pend


def gerar_s2200(colab):
    empresa = colab.empresa
    _pendencias(_pendencias_colaborador(colab))
    d = colab.dados_trabalhistas
    raiz, evt, ident = _base("S-2200", empresa)
    t = _sub(evt, "trabalhador")
    _sub(t, "cpfTrab", somente_digitos(colab.cpf))
    _sub(t, "nmTrab", colab.nome[:70])
    _sub(t, "sexo", d.sexo)
    _sub(t, "racaCor", d.raca_cor)
    _sub(t, "estCiv", d.estado_civil)
    _sub(t, "grauInstr", d.grau_instrucao)
    nasc = _sub(t, "nascimento")
    _sub(nasc, "dtNascto", colab.data_nascimento.isoformat())
    _sub(nasc, "paisNascto", "105")
    _sub(nasc, "paisNac", "105")
    end = _sub(_sub(t, "endereco"), "brasil")
    _sub(end, "tpLograd", d.tipo_logradouro)
    _sub(end, "dscLograd", colab.logradouro[:100])
    _sub(end, "nrLograd", colab.numero or "S/N")
    if colab.complemento:
        _sub(end, "complemento", colab.complemento[:30])
    _sub(end, "bairro", colab.bairro[:90])
    _sub(end, "cep", somente_digitos(colab.cep))
    _sub(end, "codMunic", d.codigo_municipio)
    _sub(end, "uf", colab.uf)
    for dep in colab.dependentes.all():
        dd = _sub(t, "dependente")
        _sub(dd, "tpDep", {"filho": "03", "conjuge": "01", "pai_mae": "09", "enteado": "06", "tutelado": "10",
                           "outro": "99"}[dep.parentesco])
        _sub(dd, "nmDep", dep.nome[:70])
        _sub(dd, "dtNascto", dep.data_nascimento.isoformat())
        if dep.cpf:
            _sub(dd, "cpfDep", somente_digitos(dep.cpf))
        _sub(dd, "depIRRF", "S" if dep.deduz_irrf else "N")
        _sub(dd, "depSF", "S" if dep.tem_salario_familia() else "N")
        _sub(dd, "incTrab", "S" if dep.invalido else "N")
    v = _sub(evt, "vinculo")
    _sub(v, "matricula", colab.matricula)
    _sub(v, "tpRegTrab", 1)
    _sub(v, "tpRegPrev", 1)
    _sub(v, "cadIni", "N")
    celetista = _sub(_sub(v, "infoRegimeTrab"), "infoCeletista")
    _sub(celetista, "dtAdm", colab.data_admissao.isoformat())
    _sub(celetista, "tpAdmissao", 1)
    _sub(celetista, "indAdmissao", 1)
    _sub(celetista, "tpRegJor", 1)
    _sub(celetista, "natAtividade", 1)
    _sub(_sub(celetista, "FGTS"), "opcFGTS", 1)
    contrato = _sub(v, "infoContrato")
    _sub(contrato, "codCargo", d.cbo)
    _sub(contrato, "codCateg", d.categoria_esocial)
    rem = _sub(contrato, "remuneracao")
    _sub(rem, "vrSalFx", f"{colab.salario:.2f}")
    _sub(rem, "undSalFixo", 5)
    _sub(_sub(contrato, "duracao"), "tpContr", 2 if colab.tipo_contrato == "temporario" else 1)
    loc = _sub(_sub(contrato, "localTrabalho"), "localTrabGeral")
    _sub(loc, "tpInsc", 1)
    _sub(loc, "nrInsc", somente_digitos(empresa.cnpj)[:14])
    return _salvar("S-2200", empresa, _xml(raiz), ident, colab)


def _id_demonstrativo(folha):
    return (f"13-{folha.competencia.year}" if folha.tipo != "mensal" else f"M-{folha.competencia:%Y%m}")[:30]


def gerar_s1200(folha, holerite):
    """Remuneração de um colaborador na competência."""
    empresa, colab = folha.empresa, holerite.colaborador
    itens = [i for i in holerite.itens.all() if i.tipo in ("provento", "desconto") and i.codigo not in CODIGOS_SEM_RUBRICA]
    rubricas = {r.codigo: r for r in Rubrica.objects.filter(codigo__in={i.codigo for i in itens})}
    pend = [f"Rubrica {i.codigo} ({i.descricao}) não cadastrada." for i in itens if i.codigo not in rubricas]
    if folha.tipo == "mensal":
        per_apur = _mes(folha.competencia)
        ind = "1"
    else:
        per_apur, ind = str(folha.competencia.year), "2"
    try:
        categoria = colab.dados_trabalhistas.categoria_esocial
    except ObjectDoesNotExist:
        pend.append(f"{colab}: dados trabalhistas.")
        categoria = "101"
    _pendencias(pend)
    raiz, evt, ident = _base("S-1200", empresa, periodico=True, per_apur=per_apur, ind_apuracao=ind)
    _sub(_sub(evt, "ideTrabalhador"), "cpfTrab", somente_digitos(colab.cpf))
    dm = _sub(evt, "dmDev")
    _sub(dm, "ideDmDev", _id_demonstrativo(folha))
    _sub(dm, "codCateg", categoria)
    per = _sub(_sub(dm, "infoPerApur"), "ideEstabLot")
    _sub(per, "tpInsc", 1)
    _sub(per, "nrInsc", somente_digitos(empresa.cnpj)[:14])
    _sub(per, "codLotacao", "LOT1")
    remun = _sub(per, "remunPerApur")
    _sub(remun, "matricula", colab.matricula or str(colab.pk))
    for i in itens:
        item = _sub(remun, "itensRemun")
        _sub(item, "codRubr", i.codigo)
        _sub(item, "ideTabRubr", "TAB1")
        _sub(item, "vrRubr", f"{i.valor:.2f}")
    return _salvar("S-1200", empresa, _xml(raiz), ident, colab, folha.competencia)


def gerar_s1210(folha, holerite, data_pagamento):
    empresa, colab = folha.empresa, holerite.colaborador
    if folha.tipo == "mensal":
        per_apur, ind, per_ref = _mes(folha.competencia), "1", _mes(folha.competencia)
    else:
        per_apur, ind, per_ref = str(folha.competencia.year), "2", str(folha.competencia.year)
    raiz, evt, ident = _base("S-1210", empresa, periodico=True, per_apur=per_apur, ind_apuracao=ind)
    ben = _sub(evt, "ideBenef")
    _sub(ben, "cpfBenef", somente_digitos(colab.cpf))
    pgto = _sub(ben, "infoPgto")
    _sub(pgto, "dtPgto", data_pagamento.isoformat())
    _sub(pgto, "tpPgto", 1)
    _sub(pgto, "indResBr", "S")
    det = _sub(pgto, "detPgtoFl")
    _sub(det, "perRef", per_ref)
    _sub(det, "ideDmDev", _id_demonstrativo(folha))
    _sub(det, "indPgtoTt", "S")
    _sub(det, "vrLiq", f"{holerite.liquido:.2f}")
    return _salvar("S-1210", empresa, _xml(raiz), ident, colab, folha.competencia)


def gerar_s1299(empresa, competencia):
    cfg = _config(empresa)
    raiz, evt, ident = _base("S-1299", empresa, periodico=True, per_apur=_mes(competencia))
    resp = _sub(evt, "ideRespInf")
    _sub(resp, "nmResp", cfg.contato_nome)
    _sub(resp, "cpfResp", somente_digitos(cfg.contato_cpf))
    if cfg.contato_telefone:
        _sub(resp, "telefone", somente_digitos(cfg.contato_telefone))
    if cfg.contato_email:
        _sub(resp, "email", cfg.contato_email)
    fech = _sub(evt, "infoFech")
    for tag, v in (("evtRemun", "S"), ("evtPgtos", "S"), ("evtAqProd", "N"), ("evtComProd", "N"),
                   ("evtContratAvNP", "N"), ("evtInfoComplPer", "N")):
        _sub(fech, tag, v)
    return _salvar("S-1299", empresa, _xml(raiz), ident, None, competencia)


def gerar_s2299(rescisao):
    colab, empresa = rescisao.colaborador, rescisao.empresa
    raiz, evt, ident = _base("S-2299", empresa)
    v = _sub(evt, "ideVinculo")
    _sub(v, "cpfTrab", somente_digitos(colab.cpf))
    _sub(v, "matricula", colab.matricula or str(colab.pk))
    info = _sub(evt, "infoDeslig")
    _sub(info, "mtvDeslig", MOTIVO_DESLIGAMENTO[rescisao.motivo])
    _sub(info, "dtDeslig", rescisao.data_desligamento.isoformat())
    _sub(info, "indPagtoAPI", "S" if rescisao.aviso == "indenizado" else "N")
    return _salvar("S-2299", empresa, _xml(raiz), ident, colab, rescisao.data_desligamento.replace(day=1))


# ------------------------------------------------------------------ assinatura e lote
def assinar_evento(ev):
    """Assina o evento com o certificado A1 da empresa (XMLDSig, RSA-SHA256)."""
    from apps.fiscal.models import ConfiguracaoFiscal

    try:
        cfg = ev.empresa.config_fiscal
    except (ObjectDoesNotExist, ConfiguracaoFiscal.DoesNotExist):
        raise ValidationError("Configure o certificado A1 da empresa em Fiscal → Configuração fiscal.") from None
    chave, cert = cfg.abrir_certificado()
    ev.xml_assinado = assinatura_xml.assinar(ev.xml.encode(), ev.identificador, chave, cert, "sha256").decode()
    ev.status = "assinado"
    ev.save()
    return ev


def lote_xml(eventos):
    """Envelope `envioLoteEventos` com os eventos assinados do mesmo grupo (até 50)."""
    eventos = list(eventos)
    if not eventos:
        raise ValidationError("Nenhum evento no lote.")
    if len(eventos) > 50:
        raise ValidationError("Um lote tem no máximo 50 eventos.")
    if any(not e.xml_assinado for e in eventos):
        raise ValidationError("Assine todos os eventos antes de montar o lote.")
    grupos = {GRUPO_LOTE[e.tipo] for e in eventos}
    if len(grupos) > 1 or len({e.empresa_id for e in eventos}) > 1:
        raise ValidationError("Um lote só pode ter eventos do mesmo grupo e da mesma empresa.")
    empresa = eventos[0].empresa
    raiz = etree.Element(f"{{{NS_LOTE}}}eSocial", nsmap={None: NS_LOTE})
    env = _sub(raiz, "envioLoteEventos", grupo=str(grupos.pop()))
    emp = _sub(env, "ideEmpregador")
    _sub(emp, "tpInsc", 1)
    _sub(emp, "nrInsc", somente_digitos(empresa.cnpj)[:8])
    tr = _sub(env, "ideTransmissor")
    _sub(tr, "tpInsc", 1)
    _sub(tr, "nrInsc", somente_digitos(empresa.cnpj)[:14])
    lote = _sub(env, "eventos")
    for e in eventos:
        el = _sub(lote, "evento", Id=e.identificador)
        el.append(etree.fromstring(e.xml_assinado.encode()))
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8").decode()
