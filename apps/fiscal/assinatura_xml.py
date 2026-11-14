"""Assinatura XMLDSig "enveloped" exigida nos documentos fiscais (NF-e, NFS-e): digest e assinatura RSA
sobre a forma canônica (C14N 1.0) do elemento identificado por Id, com o certificado em X509Certificate."""

import base64
import hashlib

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
ALGS = {"sha1": (hashes.SHA1, hashlib.sha1, DS + "rsa-sha1", DS + "sha1"),
        "sha256": (hashes.SHA256, hashlib.sha256, "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                   "http://www.w3.org/2001/04/xmlenc#sha256")}


def _c14n(el):
    return etree.tostring(el, method="c14n", exclusive=False, with_comments=False)


def _alvo(raiz, ref_id):
    achados = raiz.xpath("//*[@Id=$i]", i=ref_id)
    if len(achados) != 1:
        raise ValueError(f"Elemento com Id={ref_id!r} não encontrado (ou repetido) no XML.")
    return achados[0]


def assinar(xml: bytes, ref_id: str, chave, cert, algoritmo="sha1") -> bytes:
    """Devolve o XML com <Signature> inserida logo após o elemento assinado (como pedem SEFAZ e prefeituras)."""
    h_cls, h_fn, uri_sig, uri_dig = ALGS[algoritmo]
    raiz = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True))
    alvo = _alvo(raiz, ref_id)
    digest = base64.b64encode(h_fn(_c14n(alvo)).digest()).decode()
    E = lambda tag, **at: etree.Element(f"{{{DS}}}{tag}", **at)  # noqa: E731
    sig = etree.Element(f"{{{DS}}}Signature", nsmap={None: DS})
    si = etree.SubElement(sig, f"{{{DS}}}SignedInfo")
    si.append(E("CanonicalizationMethod", Algorithm=C14N))
    si.append(E("SignatureMethod", Algorithm=uri_sig))
    ref = etree.SubElement(si, f"{{{DS}}}Reference", URI=f"#{ref_id}")
    tr = etree.SubElement(ref, f"{{{DS}}}Transforms")
    tr.append(E("Transform", Algorithm=DS + "enveloped-signature"))
    tr.append(E("Transform", Algorithm=C14N))
    ref.append(E("DigestMethod", Algorithm=uri_dig))
    etree.SubElement(ref, f"{{{DS}}}DigestValue").text = digest
    alvo.addnext(sig)   # precisa estar no documento antes de canonicalizar o SignedInfo (namespaces em escopo)
    assinatura = chave.sign(_c14n(si), padding.PKCS1v15(), h_cls())
    etree.SubElement(sig, f"{{{DS}}}SignatureValue").text = base64.b64encode(assinatura).decode()
    x = etree.SubElement(etree.SubElement(sig, f"{{{DS}}}KeyInfo"), f"{{{DS}}}X509Data")
    etree.SubElement(x, f"{{{DS}}}X509Certificate").text = base64.b64encode(
        cert.public_bytes(serialization.Encoding.DER)).decode()
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8")


def verificar(xml: bytes) -> bool:
    """Confere digest e assinatura com o certificado embutido. Não avalia a cadeia de confiança."""
    raiz = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    ns = {"d": DS}
    try:
        sig = raiz.xpath("//d:Signature", namespaces=ns)[0]
        si = sig.find("d:SignedInfo", ns)
        ref = si.find("d:Reference", ns)
        alg = next(k for k, v in ALGS.items() if v[2] == si.find("d:SignatureMethod", ns).get("Algorithm"))
        h_cls, h_fn = ALGS[alg][0], ALGS[alg][1]
        cert = x509.load_der_x509_certificate(base64.b64decode(sig.find(".//d:X509Certificate", ns).text))
        valor = base64.b64decode(sig.find("d:SignatureValue", ns).text)
        si_c14n = _c14n(si)
        alvo = _alvo(raiz, ref.get("URI")[1:])
        pai = sig.getparent()
        pai.remove(sig)   # transform enveloped: o digest é do elemento sem a assinatura
        ok = base64.b64encode(h_fn(_c14n(alvo)).digest()).decode() == ref.find("d:DigestValue", ns).text
        cert.public_key().verify(valor, si_c14n, padding.PKCS1v15(), h_cls())
        return ok
    except Exception:
        return False
