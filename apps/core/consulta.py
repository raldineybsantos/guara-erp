"""Consulta de CNPJ na BrasilAPI (dados públicos da Receita Federal). CPF não tem consulta pública gratuita:
para ele só se valida o dígito verificador (apps.core.validators)."""
import json
import urllib.error
import urllib.request

from django.core.exceptions import ValidationError

from .validators import cnpj_valido, somente_digitos

URL = "https://brasilapi.com.br/api/cnpj/v1/{}"
SITUACOES_INATIVAS = ("BAIXADA", "INAPTA", "SUSPENSA", "NULA")


def _txt(v):
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def consultar_cnpj(cnpj, timeout=8):
    """Devolve um dict pronto para preencher formulários. Levanta ValidationError com mensagem amigável."""
    d = somente_digitos(cnpj)
    if not cnpj_valido(d):
        raise ValidationError("CNPJ inválido.")
    req = urllib.request.Request(URL.format(d), headers={"User-Agent": "GuaraERP", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise ValidationError("CNPJ não encontrado na base da Receita Federal.")
        raise ValidationError("Serviço de consulta indisponível no momento. Tente novamente em instantes.")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise ValidationError("Não foi possível consultar o CNPJ agora (sem acesso à internet ou serviço fora do ar).")
    ddd = _txt(dados.get("ddd_telefone_1"))
    situacao = _txt(dados.get("descricao_situacao_cadastral")).upper()
    return {
        "razao_social": _txt(dados.get("razao_social")),
        "nome_fantasia": _txt(dados.get("nome_fantasia")),
        "email": _txt(dados.get("email")).lower(),
        "telefone": ddd,
        "cep": _txt(dados.get("cep")),
        "logradouro": " ".join(x for x in (_txt(dados.get("descricao_tipo_de_logradouro")),
                                           _txt(dados.get("logradouro"))) if x),
        "numero": _txt(dados.get("numero")),
        "complemento": _txt(dados.get("complemento")),
        "bairro": _txt(dados.get("bairro")),
        "cidade": _txt(dados.get("municipio")),
        "uf": _txt(dados.get("uf")),
        "codigo_municipio": _txt(dados.get("codigo_municipio_ibge") or dados.get("codigo_municipio")),
        "cnae_principal": _txt(dados.get("cnae_fiscal")),
        "situacao": situacao,
        "inativa": situacao in SITUACOES_INATIVAS,
    }
