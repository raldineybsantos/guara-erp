# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contrato comum dos gateways (Asaas, Efí): o ERP conversa com este formato, nunca com o JSON de cada provedor."""
import datetime
from dataclasses import dataclass, field
from decimal import Decimal


class GatewayErro(Exception):
    """Falha na chamada ao gateway; a mensagem pode ser mostrada ao usuário."""

    def __init__(self, mensagem, status=None):
        super().__init__(mensagem)
        self.status = status


class GatewayParcial(GatewayErro):
    """A cobrança foi criada no gateway, mas faltou buscar boleto/QR. Não se deve desfazer o que já existe lá."""


# situações normalizadas
PAGO, ABERTO, CANCELADO, ESTORNADO, OUTRO = "pago", "aberto", "cancelado", "estornado", "outro"


@dataclass
class Pagamento:
    """Situação de uma cobrança no gateway, JÁ CONSULTADA na API (nunca copiada de um aviso)."""

    id: str
    referencia: str                       # o que o ERP mandou ao criar (confere com a cobrança)
    situacao: str                         # PAGO, ABERTO, CANCELADO, ESTORNADO, OUTRO
    bruto: str = ""                       # situação original do provedor (para exibir)
    valor: Decimal = Decimal("0")
    data: datetime.date | None = None
    liquido: Decimal | None = None        # None = o provedor não informou (a tarifa não é lançada)
    forma: str = "boleto"                 # pix | boleto | cartao_credito
    recibo: str = ""
    extra: dict = field(default_factory=dict)
