"""Assinatura digital de documentos com certificado ICP-Brasil (A1, arquivo .pfx/.p12).

- PDF  -> PAdES (assinatura embutida no próprio PDF, verificável no Adobe Reader e no
          Verificador de Conformidade do ITI: https://verificador.iti.gov.br). Novas assinaturas
          são incrementais: as anteriores continuam válidas (vários signatários).
- Outros arquivos -> CAdES destacada (.p7s), padrão aceito pelo verificador do ITI.

Segurança: o arquivo do certificado e a senha são usados apenas em memória durante a
requisição e NUNCA são gravados em disco, banco ou log.
"""
import datetime
import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs7, pkcs12
from django.conf import settings
from django.utils import timezone

from apps.core.validators import somente_digitos

# O pyHanko registra atributos CMS (ex.: signing_certificate_v2) no asn1crypto ao ser importado.
# Ele precisa ser carregado ANTES de qualquer uso do asn1crypto.cms, que guarda esses nomes em cache.
import pyhanko.sign.signers  # noqa: E402,F401  isort:skip

# OIDs da ICP-Brasil (DOC-ICP-04): dados do titular no "otherName" do certificado
OID_PF_DADOS = "2.16.76.1.3.1"      # pessoa física: nascimento(8) + CPF(11) + ...
OID_PJ_CNPJ = "2.16.76.1.3.3"       # pessoa jurídica: CNPJ
OID_PJ_RESPONSAVEL = "2.16.76.1.3.4"  # responsável pelo e-CNPJ: nascimento(8) + CPF(11) + ...


class ErroAssinatura(Exception):
    """Erro com mensagem segura para mostrar ao usuário (nunca contém a senha)."""


@dataclass
class DadosCertificado:
    titular: str
    cpf: str = ""
    cnpj: str = ""
    emissor: str = ""
    serial: str = ""
    valido_de: datetime.datetime | None = None
    valido_ate: datetime.datetime | None = None
    icp_brasil: bool = False
    avisos: list = field(default_factory=list)

    @property
    def documento(self):
        return self.cnpj or self.cpf


def _texto_other_name(valor_der):
    """Extrai o texto de um otherName da ICP-Brasil (OCTET STRING / PrintableString / UTF8String)."""
    try:
        from asn1crypto.core import Any
        bruto = Any.load(valor_der).parsed if valor_der else b""
        conteudo = bruto.native if hasattr(bruto, "native") else bruto
        if isinstance(conteudo, bytes):
            conteudo = conteudo.decode("latin-1", "ignore")
        return str(conteudo)
    except Exception:
        return valor_der.decode("latin-1", "ignore") if isinstance(valor_der, bytes) else ""


def ler_certificado(cert: x509.Certificate) -> DadosCertificado:
    def attr(nome, oid):
        vals = nome.get_attributes_for_oid(oid)
        return vals[0].value if vals else ""

    cn = attr(cert.subject, x509.NameOID.COMMON_NAME)
    dados = DadosCertificado(
        titular=cn.split(":")[0].strip() or cn, emissor=attr(cert.issuer, x509.NameOID.COMMON_NAME),
        serial=format(cert.serial_number, "X"), valido_de=cert.not_valid_before_utc, valido_ate=cert.not_valid_after_utc,
    )
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for nome in (n for n in san if isinstance(n, x509.OtherName)):
            oid, texto = nome.type_id.dotted_string, somente_digitos(_texto_other_name(nome.value))
            if oid == OID_PF_DADOS and len(texto) >= 19:
                dados.cpf = texto[8:19]
            elif oid == OID_PJ_CNPJ and len(texto) >= 14:
                dados.cnpj = texto[:14]
            elif oid == OID_PJ_RESPONSAVEL and len(texto) >= 19 and not dados.cpf:
                dados.cpf = texto[8:19]
    except x509.ExtensionNotFound:
        pass
    if not dados.documento and ":" in cn:  # padrão ICP-Brasil: "NOME DO TITULAR:12345678900"
        doc = somente_digitos(cn.rsplit(":", 1)[1])
        if len(doc) == 11:
            dados.cpf = doc
        elif len(doc) == 14:
            dados.cnpj = doc
    # Certificados ICP-Brasil têm O=ICP-Brasil no emissor (as AC credenciadas pelo ITI)
    dados.icp_brasil = any(a.value == "ICP-Brasil" for a in cert.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME))
    if not dados.icp_brasil:
        dados.avisos.append("O certificado não parece ser ICP-Brasil (pode ser de teste ou de outra autoridade).")
    return dados


def carregar_pfx(pfx_bytes: bytes, senha: str):
    """Abre o .pfx/.p12 em memória. Retorna (chave, certificado, cadeia, dados)."""
    if not pfx_bytes:
        raise ErroAssinatura("Selecione o arquivo do certificado (.pfx ou .p12).")
    try:
        chave, cert, cadeia = pkcs12.load_key_and_certificates(pfx_bytes, (senha or "").encode("utf-8"))
    except ValueError:
        raise ErroAssinatura("Não foi possível abrir o certificado: senha incorreta ou arquivo inválido.") from None
    if cert is None or chave is None:
        raise ErroAssinatura("O arquivo não contém certificado com chave privada.")
    dados = ler_certificado(cert)
    agora = timezone.now()
    if not (dados.valido_de <= agora <= dados.valido_ate):
        raise ErroAssinatura(f"Certificado fora da validade ({dados.valido_de:%d/%m/%Y} a {dados.valido_ate:%d/%m/%Y}).")
    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        if not (ku.digital_signature or ku.content_commitment):
            raise ErroAssinatura("O certificado não permite assinatura digital (uso de chave).")
    except x509.ExtensionNotFound:
        pass
    return chave, cert, list(cadeia or []), dados


def conferir_titular(usuario, dados: DadosCertificado):
    """Impede assinar com certificado de outra pessoa.

    - e-CPF: o CPF do certificado deve ser o mesmo do colaborador vinculado ao usuário.
    - e-CNPJ: o CNPJ deve ser de uma empresa cadastrada e o usuário precisa da permissão
      "ged.assinar_ecnpj" (representante legal).
    """
    from apps.core.models import Empresa

    if dados.cnpj:
        if not usuario.has_perm("ged.assinar_ecnpj"):
            raise ErroAssinatura("Você não tem permissão para assinar com certificado da empresa (e-CNPJ).")
        if not any(somente_digitos(c) == dados.cnpj for c in Empresa.objects.values_list("cnpj", flat=True)):
            raise ErroAssinatura("O CNPJ do certificado não corresponde a nenhuma empresa cadastrada.")
        return
    colab = getattr(usuario, "colaborador", None)
    if dados.cpf and colab is not None and somente_digitos(colab.cpf) != dados.cpf:
        raise ErroAssinatura("O CPF do certificado não é o mesmo do seu cadastro de colaborador. "
                             "Cada pessoa deve assinar com o próprio certificado.")
    if dados.cpf and colab is None:
        dados.avisos.append("Seu usuário não está vinculado a um colaborador: o CPF do certificado não pôde ser conferido.")


def _carimbador():
    """Carimbo do tempo opcional (PAdES-T) se ASSINATURA_TSA_URL estiver configurado."""
    url = getattr(settings, "ASSINATURA_TSA_URL", "")
    if not url:
        return None
    from pyhanko.sign.timestamps import HTTPTimeStamper
    return HTTPTimeStamper(url)


def assinar_pdf(pdf_bytes: bytes, pfx_bytes: bytes, senha: str, nome_campo: str, motivo="", local="") -> bytes:
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers

    try:
        signer = signers.SimpleSigner.load_pkcs12_data(pfx_bytes, other_certs=[], passphrase=(senha or "").encode())
    except Exception:
        raise ErroAssinatura("Não foi possível abrir o certificado: senha incorreta ou arquivo inválido.") from None
    if signer is None:
        raise ErroAssinatura("Não foi possível abrir o certificado.")
    try:
        w = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes), strict=False)
        meta = signers.PdfSignatureMetadata(field_name=nome_campo, md_algorithm="sha256", reason=motivo or None,
                                            location=local or None, subfilter=fields.SigSeedSubFilter.PADES)
        saida = signers.PdfSigner(meta, signer=signer, timestamper=_carimbador()).sign_pdf(w)
        return saida.getvalue()
    except ErroAssinatura:
        raise
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Falha ao assinar PDF: %s", type(exc).__name__)
        raise ErroAssinatura(f"Falha ao assinar o PDF: {type(exc).__name__}. O arquivo pode estar protegido ou corrompido.") from None


def assinar_cades(conteudo: bytes, chave, cert, cadeia) -> bytes:
    """Assinatura CAdES-BES destacada (.p7s, DER) com SHA-256."""
    if not isinstance(chave, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise ErroAssinatura("Tipo de chave do certificado não suportado (use RSA ou ECDSA).")
    builder = pkcs7.PKCS7SignatureBuilder().set_data(conteudo).add_signer(cert, chave, hashes.SHA256())
    for c in cadeia:
        builder = builder.add_certificate(c)
    return builder.sign(serialization.Encoding.DER,
                        [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])


# --------------------------------------------------------------------------- verificação
@dataclass
class ResultadoVerificacao:
    titular: str
    documento: str
    emissor: str
    data: datetime.datetime | None
    integro: bool
    valido: bool
    confiavel: bool
    cobre_documento_inteiro: bool = True
    detalhe: str = ""


def _contexto_confianca():
    """Raízes confiáveis: certificados .crt/.pem/.cer da pasta ICP_BRASIL_CAS_DIR (ICP-Brasil v5, v10...)."""
    from asn1crypto import pem
    from asn1crypto import x509 as ax509
    from pyhanko_certvalidator import ValidationContext

    raizes = []
    pasta = getattr(settings, "ICP_BRASIL_CAS_DIR", "")
    if pasta and Path(pasta).is_dir():
        for arq in Path(pasta).iterdir():
            if arq.suffix.lower() in (".crt", ".pem", ".cer"):
                dados = arq.read_bytes()
                for _, _, der in (pem.unarmor(dados, multiple=True) if pem.detect(dados) else [(None, None, dados)]):
                    try:
                        raizes.append(ax509.Certificate.load(der))
                    except Exception:
                        pass
    return ValidationContext(trust_roots=raizes, allow_fetching=False), bool(raizes)


def verificar_pdf(pdf_bytes: bytes):
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature

    try:
        leitor = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
        assinaturas = list(leitor.embedded_signatures)
    except Exception:
        return []
    vc, tem_raizes = _contexto_confianca()
    resultado = []
    for sig in assinaturas:
        try:
            st = validate_pdf_signature(sig, vc)
            cert = x509.load_der_x509_certificate(st.signing_cert.dump())
            dados = ler_certificado(cert)
            cobertura = getattr(st.coverage, "name", "")
            resultado.append(ResultadoVerificacao(
                titular=dados.titular, documento=dados.documento, emissor=dados.emissor,
                data=st.signer_reported_dt, integro=bool(st.intact), valido=bool(st.intact and st.valid),
                confiavel=bool(st.trusted), cobre_documento_inteiro=cobertura in ("ENTIRE_FILE", "ENTIRE_REVISION"),
                detalhe="" if tem_raizes else "Cadeia ICP-Brasil não configurada no servidor (ICP_BRASIL_CAS_DIR).",
            ))
        except Exception as exc:
            resultado.append(ResultadoVerificacao("?", "", "", None, False, False, False, False,
                                                  f"Não foi possível validar: {type(exc).__name__}"))
    return resultado


def verificar_cades(conteudo: bytes, p7s: bytes):
    """Confere uma assinatura CAdES destacada: resumo (messageDigest) e assinatura dos atributos."""
    from asn1crypto import cms

    try:
        info = cms.ContentInfo.load(p7s)
        sd = info["content"]
        signer = sd["signer_infos"][0]
        certs = [c.chosen for c in sd["certificates"]]
        sid = signer["sid"].chosen
        cert_asn = next(c for c in certs if c.serial_number == sid["serial_number"].native)
        cert = x509.load_der_x509_certificate(cert_asn.dump())
        alg_hash = signer["digest_algorithm"]["algorithm"].native
        h = {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512(), "sha1": hashes.SHA1()}[alg_hash]
        attrs = signer["signed_attrs"]
        md = next(a["values"][0].native for a in attrs if a["type"].native == "message_digest")
        dig = hashes.Hash(h); dig.update(conteudo); integro = dig.finalize() == md
        dados_assinados = attrs.untag().dump()
        assinatura = signer["signature"].native
        pub = cert.public_key()
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(assinatura, dados_assinados, padding.PKCS1v15(), h)
        else:
            pub.verify(assinatura, dados_assinados, ec.ECDSA(h))
        sign_time = next((a["values"][0].native for a in attrs if a["type"].native == "signing_time"), None)
        dados = ler_certificado(cert)
        return [ResultadoVerificacao(dados.titular, dados.documento, dados.emissor, sign_time, integro, integro, False,
                                     True, "" if integro else "O arquivo foi alterado depois da assinatura.")]
    except Exception as exc:
        return [ResultadoVerificacao("?", "", "", None, False, False, False, False,
                                     f"Assinatura inválida ou não reconhecida ({type(exc).__name__}).")]


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def nome_campo_unico(pdf_bytes: bytes) -> str:
    existentes = set(re.findall(rb"/T\s*\((Assinatura\d+)\)", pdf_bytes))
    n = 1
    while f"Assinatura{n}".encode() in existentes:
        n += 1
    return f"Assinatura{n}"
