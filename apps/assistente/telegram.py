"""Canal Telegram. Regras de segurança:

- o webhook só aceita chamadas com o cabeçalho secreto combinado com o Telegram (comparação em tempo constante);
- só responde a chats VINCULADOS a um usuário do ERP, por código de uso único gerado dentro do ERP (10 minutos);
- o chat externo nunca grava nem consulta dados: só cria uma proposta, que o usuário confere e salva no ERP;
- a resposta não repete os dados do cadastro, e as permissões são as do usuário vinculado.
"""

import hmac
import json
import urllib.request

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.fiscal import cofre

from . import servico
from .models import VinculoCanal

AJUDA = ("Este chat não está vinculado a um usuário do Guará ERP. No ERP, abra Assistente de IA → Vincular "
         "Telegram, gere um código e envie aqui: /vincular CODIGO")


def segredo_confere(config, recebido):
    return bool(config.telegram_segredo) and hmac.compare_digest(config.telegram_segredo, recebido or "")


def enviar(config, chat_id, texto):
    if not config.telegram_token:
        return
    token = cofre.decifrar(config.telegram_token).decode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", method="POST",
                                 data=json.dumps({"chat_id": chat_id, "text": texto[:1000]}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).close()   # noqa: S310
    except Exception:   # resposta ao chat é melhor esforço; a proposta já está no ERP
        pass


def tratar(config, update):
    """Processa um update do Telegram e devolve o texto da resposta (ou None para ignorar)."""
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    texto = (msg.get("text") or "").strip()
    if chat.get("type") != "private" or not texto or not chat.get("id"):
        return None, None   # grupos e mensagens sem texto são ignorados
    chat_id = str(chat["id"])
    if texto.lower().startswith("/vincular"):
        codigo = texto[9:].strip().upper()
        v = VinculoCanal.objects.filter(codigo=codigo, codigo_expira__gt=timezone.now()).first() if codigo else None
        if v is None:
            return chat_id, "Código inválido ou vencido. Gere outro no ERP."
        VinculoCanal.objects.filter(chat_id=chat_id).exclude(pk=v.pk).update(chat_id="")
        v.chat_id, v.codigo, v.codigo_expira, v.vinculado_em = chat_id, "", None, timezone.now()
        v.save()
        return chat_id, "Chat vinculado. Agora é só escrever o que você quer cadastrar."
    v = VinculoCanal.objects.filter(chat_id=chat_id).select_related("usuario").first()
    if v is None or not v.usuario.is_active:
        return chat_id, AJUDA
    if texto.lower() in ("/desvincular", "/sair"):
        v.chat_id = ""
        v.save(update_fields=["chat_id"])
        return chat_id, "Chat desvinculado."
    try:
        return chat_id, servico.resumo(servico.interpretar(v.usuario, texto, "telegram", consultar=False))
    except ValidationError as e:
        return chat_id, " ".join(e.messages)


