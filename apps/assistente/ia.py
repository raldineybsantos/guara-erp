"""Chamada ao modelo de linguagem. Um único ponto de saída, para os três provedores, sempre pedindo JSON."""

import json
import urllib.error
import urllib.request

from django.core.exceptions import ValidationError

from apps.fiscal import cofre

PADRAO = {"ollama": "http://localhost:11434", "openai": "https://api.openai.com", "anthropic": "https://api.anthropic.com"}


def _post(url, corpo, cabecalhos, tempo):
    if not url.startswith(("http://", "https://")):
        raise ValidationError("Endereço do serviço de IA inválido.")
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **cabecalhos})
    try:
        with urllib.request.urlopen(req, timeout=tempo) as r:   # noqa: S310 - endereço definido pelo administrador
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise ValidationError(f"O serviço de IA respondeu com erro {e.code}. Confira modelo e chave.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValidationError("Não foi possível falar com o serviço de IA. Confira o endereço e se ele está no ar.") \
            from None
    except ValueError:
        raise ValidationError("O serviço de IA devolveu uma resposta que não é JSON.") from None


def chamar(config, sistema, usuario):
    """Devolve o texto da resposta do modelo (esperado: um objeto JSON)."""
    if config.provedor == "desligado":
        raise ValidationError("O assistente de IA está desligado. O administrador liga em Administração → "
                              "Configuração da IA.")
    if not config.modelo:
        raise ValidationError("Informe o modelo na configuração da IA.")
    base = (config.url_base or PADRAO[config.provedor]).rstrip("/")
    chave = cofre.decifrar(config.chave_api).decode() if config.chave_api else ""
    t = config.tempo_limite
    if config.provedor == "ollama":
        r = _post(f"{base}/api/chat", {"model": config.modelo, "stream": False, "format": "json",
                                       "options": {"temperature": 0},
                                       "messages": [{"role": "system", "content": sistema},
                                                    {"role": "user", "content": usuario}]}, {}, t)
        return r.get("message", {}).get("content", "")
    if config.provedor == "openai":
        r = _post(f"{base}/v1/chat/completions",
                  {"model": config.modelo, "temperature": 0, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]},
                  {"Authorization": f"Bearer {chave}"}, t)
        return r["choices"][0]["message"]["content"]
    r = _post(f"{base}/v1/messages", {"model": config.modelo, "max_tokens": 1024, "system": sistema,
                                      "messages": [{"role": "user", "content": usuario}]},
              {"x-api-key": chave, "anthropic-version": "2023-06-01"}, t)
    return "".join(b.get("text", "") for b in r.get("content", []))


def extrair_json(texto):
    """O primeiro objeto JSON do texto (modelos às vezes cercam com crases ou comentários)."""
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim <= ini:
        raise ValidationError("A IA não devolveu uma resposta utilizável. Tente reformular o pedido.")
    try:
        obj = json.loads(texto[ini:fim + 1])
    except ValueError:
        raise ValidationError("A IA não devolveu uma resposta utilizável. Tente reformular o pedido.") from None
    if not isinstance(obj, dict):
        raise ValidationError("A IA não devolveu uma resposta utilizável.")
    return obj
