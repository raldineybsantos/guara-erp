"""Gera certificados de TESTE (não ICP-Brasil) no formato e-CPF / e-CNPJ, para homologação e testes.

NUNCA use em documentos reais: não têm validade jurídica.
"""
import datetime

from asn1crypto.core import OctetString
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from .assinatura import OID_PF_DADOS, OID_PJ_CNPJ


def gerar_pfx(nome, cpf="", cnpj="", senha="teste123", dias=365, nascimento="01011990"):
    agora = datetime.datetime.now(datetime.timezone.utc)
    chave_ac = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome_ac = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Guara ERP - TESTE"),
                         x509.NameAttribute(NameOID.COMMON_NAME, "AC de Teste Guara ERP (NAO E ICP-Brasil)")])
    cert_ac = (x509.CertificateBuilder().subject_name(nome_ac).issuer_name(nome_ac).public_key(chave_ac.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(days=1))
               .not_valid_after(agora + datetime.timedelta(days=dias + 30))
               .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
               .sign(chave_ac, hashes.SHA256()))

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    doc = cnpj or cpf
    nomes_alt = []
    if cpf:
        nomes_alt.append(x509.OtherName(x509.ObjectIdentifier(OID_PF_DADOS),
                                        OctetString((nascimento + cpf + "0" * 11 + "0" * 15).encode()).dump()))
    if cnpj:
        nomes_alt.append(x509.OtherName(x509.ObjectIdentifier(OID_PJ_CNPJ), OctetString(cnpj.encode()).dump()))
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
                                        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Guara ERP - TESTE"),
                                        x509.NameAttribute(NameOID.COMMON_NAME, f"{nome.upper()}:{doc}")]))
               .issuer_name(nome_ac).public_key(chave.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(min(agora, agora + datetime.timedelta(days=dias)) - datetime.timedelta(days=30))
               .not_valid_after(agora + datetime.timedelta(days=dias))
               .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=True,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                            crl_sign=False, encipher_only=False, decipher_only=False), critical=True))
    if nomes_alt:
        builder = builder.add_extension(x509.SubjectAlternativeName(nomes_alt), critical=False)
    cert = builder.sign(chave_ac, hashes.SHA256())
    return pkcs12.serialize_key_and_certificates(
        nome.encode(), chave, cert, [cert_ac],
        serialization.BestAvailableEncryption(senha.encode()) if senha else serialization.NoEncryption())
