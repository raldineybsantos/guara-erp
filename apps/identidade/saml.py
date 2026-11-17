# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""SAML 2.0 — ERP como Service Provider (SP-initiated, binding Redirect na ida e POST na volta).

Defesas: XML sem DOCTYPE/entidades; assinatura XML verificada (signxml, SHA-256+) contra o certificado do provedor e só o
elemento ASSINADO é usado (anti *signature wrapping*); IDs únicos; Issuer, Audience, Destination/Recipient, InResponseTo,
janela de validade (±2 min) e confirmação *bearer* conferidos; asserção usada uma única vez; asserção cifrada recusada.
Não há login iniciado pelo provedor (exigiria aceitar resposta sem pedido nosso)."""
import base64
import datetime
import hmac
import secrets
import zlib
from urllib.parse import urlencode

from xml.sax.saxutils import escape, quoteattr

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone
from lxml import etree

from .models import AssercaoSAML, IdentidadeExterna, PedidoSAML, ProvedorSAML

NS = {"samlp": "urn:oasis:names:tc:SAML:2.0:protocol", "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
      "ds": "http://www.w3.org/2000/09/xmldsig#", "md": "urn:oasis:names:tc:SAML:2.0:metadata"}
TAMANHO_MAX = 1_000_000
TOLERANCIA = datetime.timedelta(minutes=2)
VALIDADE_PEDIDO = 600
COOKIE = "guara_saml"
ATRIBUTOS_EMAIL = ("email", "mail", "emailaddress", "urn:oid:0.9.2342.19200300.100.1.3",
                   "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
ATRIBUTOS_NOME = ("displayname", "name", "http://schemas.microsoft.com/identity/claims/displayname",
                  "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name", "urn:oid:2.16.840.1.113730.3.1.241")


class SAMLErro(Exception):
    """Falha de login SAML; a mensagem pode ser mostrada ao usuário."""


def base_url(request):
    return getattr(settings, "ERP_URL_BASE", "") or request.build_absolute_uri("/").rstrip("/")


def urls(request, prov):
    b = base_url(request)
    return {"entity_id": f"{b}/identidade/saml/{prov.slug}/metadata/", "acs": f"{b}/identidade/saml/{prov.slug}/acs/",
            "metadata": f"{b}/identidade/saml/{prov.slug}/metadata/"}


def metadados(request, prov) -> bytes:
    u = urls(request, prov)
    raiz = etree.Element(f"{{{NS['md']}}}EntityDescriptor", nsmap={"md": NS["md"]}, entityID=u["entity_id"])
    sp = etree.SubElement(raiz, f"{{{NS['md']}}}SPSSODescriptor", AuthnRequestsSigned="false",
                          WantAssertionsSigned="true", protocolSupportEnumeration=NS["samlp"])
    etree.SubElement(sp, f"{{{NS['md']}}}NameIDFormat").text = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
    etree.SubElement(sp, f"{{{NS['md']}}}AssertionConsumerService", Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                     Location=u["acs"], index="0", isDefault="true")
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def _agora_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iniciar(request, prov, proximo="/"):
    """Monta o AuthnRequest (binding Redirect: deflate + base64) e grava o pedido. Devolve (url_do_provedor, segredo).

    O segredo vai num cookie SameSite=None (única forma de ele acompanhar o POST vindo do provedor) e amarra a resposta
    ao navegador que começou o login — impede injetar no navegador da vítima uma resposta que o atacante obteve."""
    u = urls(request, prov)
    req_id = "_" + secrets.token_hex(20)
    xml = (f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="{req_id}" Version="2.0" '
           f'IssueInstant="{_agora_iso()}" Destination={quoteattr(prov.idp_sso_url)} '
           f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" AssertionConsumerServiceURL={quoteattr(u["acs"])}>'
           f'<saml:Issuer>{escape(u["entity_id"])}</saml:Issuer>'
           f'<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/>'
           f'</samlp:AuthnRequest>').encode()
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    corpo = base64.b64encode(comp.compress(xml) + comp.flush()).decode()
    relay, vinculo = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    agora = timezone.now()
    PedidoSAML.objects.filter(expira_em__lt=agora).delete()
    PedidoSAML.objects.create(relay=relay, id_pedido=req_id, provedor=prov, proximo=proximo[:300], vinculo=vinculo,
                              expira_em=agora + datetime.timedelta(seconds=VALIDADE_PEDIDO))
    sep = "&" if "?" in prov.idp_sso_url else "?"
    return prov.idp_sso_url + sep + urlencode({"SAMLRequest": corpo, "RelayState": relay}), vinculo


def _parse(dados: bytes):
    if len(dados) > TAMANHO_MAX:
        raise SAMLErro("resposta grande demais")
    baixo = dados.lower()
    if b"<!doctype" in baixo or b"<!entity" in baixo:
        raise SAMLErro("resposta inválida")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, dtd_validation=False,
                             huge_tree=False, remove_comments=True)
    try:
        return etree.fromstring(dados, parser)
    except etree.XMLSyntaxError:
        raise SAMLErro("resposta ilegível")


def _verificar_assinatura(raiz, prov):
    """Devolve a asserção CONFIAVEL (a que está coberta por assinatura válida) ou levanta SAMLErro."""
    from signxml import XMLVerifier
    from signxml.exceptions import SignXMLException

    ids = [e.get("ID") for e in raiz.iter() if isinstance(e.tag, str) and e.get("ID")]
    if len(ids) != len(set(ids)):
        raise SAMLErro("resposta com identificadores repetidos")
    if raiz.findall(".//saml:EncryptedAssertion", NS):
        raise SAMLErro("asserção cifrada não é suportada: configure o provedor para enviar asserção assinada e sem cifra")
    assercoes = raiz.findall(".//saml:Assertion", NS)
    if len(assercoes) != 1:
        raise SAMLErro("a resposta deve conter exatamente uma asserção")
    assercao = assercoes[0]
    candidatos = [raiz] if raiz.find("ds:Signature", NS) is not None else []
    if assercao.find("ds:Signature", NS) is not None:
        candidatos.append(assercao)
    if not candidatos:
        raise SAMLErro("resposta sem assinatura")
    for alvo in candidatos:
        # verifica uma CÓPIA isolada: a referência da assinatura tem de apontar para o próprio elemento
        copia = etree.fromstring(etree.tostring(alvo))
        for pem in prov.certificados:
            try:
                r = XMLVerifier().verify(copia, x509_cert=pem, expect_references=1)
            except (SignXMLException, ValueError, etree.LxmlError):
                continue
            assinado = r.signed_xml
            if assinado.tag == f"{{{NS['saml']}}}Assertion":
                return assinado
            if assinado.tag == f"{{{NS['samlp']}}}Response":
                achadas = assinado.findall("saml:Assertion", NS)
                if len(achadas) == 1:
                    return achadas[0]
    raise SAMLErro("assinatura inválida")


def _instante(texto):
    try:
        return datetime.datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(datetime.timezone.utc)
    except (ValueError, AttributeError):
        raise SAMLErro("data inválida na resposta")


def validar_retorno(request, prov, post) -> tuple[dict, str]:
    """Valida a resposta SAML. Devolve ({'subject','email','nome'}, próximo_destino)."""
    relay = str(post.get("RelayState", ""))[:64]
    ped = PedidoSAML.objects.filter(relay=relay, provedor=prov).first() if relay else None
    if ped is not None:
        ped.delete()                                                    # uso único, valendo ou não
    if ped is None or ped.expira_em < timezone.now():
        raise SAMLErro("o pedido de login expirou ou não foi iniciado por aqui; tente de novo")
    if request.is_secure() and not hmac.compare_digest(request.COOKIES.get(COOKIE, ""), ped.vinculo):
        raise SAMLErro("a resposta não veio do mesmo navegador que iniciou o login")
    pedido = {"id": ped.id_pedido, "next": ped.proximo}
    try:
        bruto = base64.b64decode(post.get("SAMLResponse", ""), validate=False)
    except ValueError:
        raise SAMLErro("resposta ilegível")
    resposta = _parse(bruto)
    if resposta.tag != f"{{{NS['samlp']}}}Response":
        raise SAMLErro("resposta inválida")
    u = urls(request, prov)
    codigo = resposta.find("samlp:Status/samlp:StatusCode", NS)
    if codigo is None or not codigo.get("Value", "").endswith(":Success"):
        raise SAMLErro("o provedor não autenticou o usuário")
    if resposta.get("InResponseTo") != pedido["id"]:
        raise SAMLErro("resposta não corresponde ao pedido")
    if resposta.get("Destination") and resposta.get("Destination") != u["acs"]:
        raise SAMLErro("resposta destinada a outro endereço")
    a = _verificar_assinatura(resposta, prov)

    emissor = a.find("saml:Issuer", NS)
    if emissor is None or (emissor.text or "").strip() != prov.idp_entity_id.strip():
        raise SAMLErro("emissor da asserção não confere")
    agora = datetime.datetime.now(datetime.timezone.utc)
    cond = a.find("saml:Conditions", NS)
    if cond is None:
        raise SAMLErro("asserção sem condições de validade")
    if cond.get("NotBefore") and agora + TOLERANCIA < _instante(cond.get("NotBefore")):
        raise SAMLErro("asserção ainda não é válida (confira o relógio do servidor)")
    if not cond.get("NotOnOrAfter") or agora - TOLERANCIA >= _instante(cond.get("NotOnOrAfter")):
        raise SAMLErro("asserção expirada")
    publicos = [(x.text or "").strip() for x in cond.findall("saml:AudienceRestriction/saml:Audience", NS)]
    if u["entity_id"] not in publicos:
        raise SAMLErro("asserção emitida para outro sistema")
    confirmou = False
    for c in a.findall("saml:Subject/saml:SubjectConfirmation", NS):
        d = c.find("saml:SubjectConfirmationData", NS)
        if (c.get("Method") == "urn:oasis:names:tc:SAML:2.0:cm:bearer" and d is not None
                and d.get("Recipient") == u["acs"] and d.get("InResponseTo") == pedido["id"]
                and d.get("NotOnOrAfter") and agora - TOLERANCIA < _instante(d.get("NotOnOrAfter"))):
            confirmou = True
    if not confirmou:
        raise SAMLErro("confirmação do usuário inválida")
    idr = a.get("ID")
    if not idr:
        raise SAMLErro("asserção sem identificador")
    AssercaoSAML.objects.filter(expira_em__lt=timezone.now()).delete()
    try:
        with transaction.atomic():
            AssercaoSAML.objects.create(id_assercao=f"{prov.pk}:{idr}"[:200],
                                        expira_em=timezone.now() + datetime.timedelta(hours=2))
    except IntegrityError:
        raise SAMLErro("esta resposta já foi usada")
    nameid = a.find("saml:Subject/saml:NameID", NS)
    if nameid is None or not (nameid.text or "").strip():
        raise SAMLErro("asserção sem identificação do usuário")
    attrs = {}
    for at in a.findall("saml:AttributeStatement/saml:Attribute", NS):
        valores = [(v.text or "").strip() for v in at.findall("saml:AttributeValue", NS) if (v.text or "").strip()]
        if valores:
            attrs[(at.get("Name") or "").lower()] = valores[0]
            if at.get("FriendlyName"):
                attrs.setdefault(at.get("FriendlyName").lower(), valores[0])
    subject = nameid.text.strip()
    email = ""
    chaves = ([prov.atributo_email.lower()] if prov.atributo_email else []) + list(ATRIBUTOS_EMAIL)
    for k in chaves:
        if attrs.get(k):
            email = attrs[k].lower()
            break
    if not email and "@" in subject:
        email = subject.lower()
    nome = ""
    for k in ([prov.atributo_nome.lower()] if prov.atributo_nome else []) + list(ATRIBUTOS_NOME):
        if attrs.get(k):
            nome = attrs[k]
            break
    return {"subject": subject, "email": email, "nome": nome}, pedido.get("next", "/")


def resolver_usuario(prov, info):
    """Mesma política do OIDC: vínculo estável pelo NameID; e-mail só vincula se o administrador permitiu."""
    from .oidc import vinculavel_por_scim

    origem, subject, email = f"saml:{prov.slug}", info["subject"][:300], info["email"]
    dominio = email.rpartition("@")[2]
    if prov.dominios and (not email or dominio not in prov.dominios):
        raise SAMLErro("o domínio do seu e-mail não está autorizado neste sistema")
    ident = IdentidadeExterna.objects.filter(origem=origem, subject=subject).select_related("usuario").first()
    if ident:
        user = ident.usuario
    else:
        if not email:
            raise SAMLErro("o provedor não informou o seu e-mail")
        candidatos = list(User.objects.filter(email__iexact=email)[:2])
        if len(candidatos) > 1:
            raise SAMLErro("há mais de um usuário com este e-mail; peça ajuda a um administrador")
        if candidatos:
            if not (prov.vincular_por_email or vinculavel_por_scim(candidatos[0])):
                raise SAMLErro("já existe um usuário com este e-mail que não está ligado a este provedor; "
                               "peça a um administrador para liberar")
            user = candidatos[0]
        elif prov.criar_usuarios:
            user = _criar(prov, email, info["nome"])
        else:
            raise SAMLErro("você ainda não tem cadastro neste sistema; peça a um administrador")
        try:
            with transaction.atomic():
                ident = IdentidadeExterna.objects.create(usuario=user, origem=origem, subject=subject, email=email)
        except IntegrityError:
            ident = IdentidadeExterna.objects.get(origem=origem, subject=subject)
    if not user.is_active:
        raise SAMLErro("usuário desativado")
    ident.ultimo_login = timezone.now()
    ident.email = email or ident.email
    ident.save(update_fields=["ultimo_login", "email"])
    return user


def _criar(prov, email, nome):
    base, i = email[:150], 2
    username = base
    while User.objects.filter(username__iexact=username).exists():
        username, i = f"{base[:140]}-{i}", i + 1
    user = User(username=username, email=email[:254], first_name=nome.partition(" ")[0][:150],
                last_name=nome.partition(" ")[2][:150])
    user.set_unusable_password()
    user.save()
    if prov.grupo_padrao_id:
        user.groups.add(prov.grupo_padrao)
    return user
