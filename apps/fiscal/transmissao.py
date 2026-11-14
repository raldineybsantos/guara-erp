"""Serviço de transmissão: escolhe o emissor, registra cada tentativa e atualiza a nota."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.models import Empresa

from . import emissores
from .models import ConfiguracaoFiscal, Transmissao


def _config(nota):
    empresa = nota.empresa or Empresa.padrao()
    config = ConfiguracaoFiscal.objects.filter(empresa=empresa).first() if empresa else None
    if config is None:
        raise ValidationError("Cadastre a configuração fiscal da empresa em Fiscal → Configuração fiscal.")
    return config


def _emissor(nota, config):
    if nota.modelo in ("55", "65"):
        return emissores.obter(config.emissor_nfe)
    if nota.modelo == "nfse":
        return emissores.obter(config.emissor_nfse)
    raise ValidationError("Só NF-e, NFC-e e NFS-e são transmitidas.")


def _registrar(nota, operacao, emissor, config, r, usuario):
    return Transmissao.objects.create(
        nota=nota, operacao=operacao, emissor=emissor.codigo, ambiente=config.ambiente,
        resultado="autorizado" if r.ok else "rejeitado", codigo=r.codigo, mensagem=r.mensagem[:500],
        protocolo=r.protocolo, xml_enviado=r.xml_enviado, xml_retorno=r.xml_retorno, usuario=usuario)


@transaction.atomic
def transmitir(nota, usuario=None):
    if nota.tipo != "saida":
        raise ValidationError("Só notas de saída são transmitidas.")
    if nota.status != "emitida":
        raise ValidationError("Emita (numere) a nota antes de transmitir.")
    if nota.situacao_fiscal in ("autorizada", "cancelada"):
        raise ValidationError("Esta nota já foi autorizada.")
    config = _config(nota)
    emissor = _emissor(nota, config)
    r = emissor.autorizar(nota, config)
    t = _registrar(nota, "autorizacao", emissor, config, r, usuario)
    nota.situacao_fiscal = "autorizada" if r.ok else "rejeitada"
    if r.ok:
        nota.protocolo, nota.chave_acesso = r.protocolo, r.chave or nota.chave_acesso
    nota.save()
    return t


@transaction.atomic
def cancelar(nota, justificativa, usuario=None):
    if nota.situacao_fiscal != "autorizada":
        raise ValidationError("Só notas autorizadas são canceladas no fisco.")
    if len((justificativa or "").strip()) < 15:
        raise ValidationError("A justificativa do cancelamento precisa ter ao menos 15 caracteres.")
    config = _config(nota)
    emissor = _emissor(nota, config)
    r = emissor.cancelar(nota, config, justificativa.strip())
    t = _registrar(nota, "cancelamento", emissor, config, r, usuario)
    if r.ok:
        nota.situacao_fiscal, nota.status = "cancelada", "cancelada"
        nota.save()
    return t
