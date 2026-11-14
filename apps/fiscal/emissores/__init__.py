"""Emissores: cada canal de transmissão (SEFAZ, Emissor Nacional da NFS-e, prefeitura de São Paulo) é uma
classe com a mesma interface. O serviço de transmissão escolhe pelo modelo da nota e pela configuração."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError


@dataclass
class Resultado:
    ok: bool
    codigo: str = ""
    mensagem: str = ""
    protocolo: str = ""
    chave: str = ""
    xml_enviado: str = ""
    xml_retorno: str = ""


class Emissor:
    codigo = ""
    nome = ""
    homologado = False     # False = canal ainda não liberado para uso

    def autorizar(self, nota, config) -> Resultado:
        raise NotImplementedError

    def cancelar(self, nota, config, justificativa) -> Resultado:
        raise NotImplementedError


class EmConstrucao(Emissor):
    """Canal previsto na arquitetura e ainda não liberado. Recusa com uma mensagem clara, sem fingir envio."""

    def _recusar(self):
        raise ValidationError(
            f"A transmissão por “{self.nome}” ainda não está liberada nesta versão. Em Fiscal → Configuração "
            "fiscal, use “Simulação interna” para testar o fluxo, ou transmita pelo emissor atual e informe "
            "chave e protocolo na nota.")

    def autorizar(self, nota, config):
        self._recusar()

    def cancelar(self, nota, config, justificativa):
        self._recusar()


class Sefaz(EmConstrucao):
    codigo, nome = "sefaz", "SEFAZ do estado (NF-e)"


class NfseNacional(EmConstrucao):
    codigo, nome = "nacional", "Emissor Nacional da NFS-e"


class NfseSaoPaulo(EmConstrucao):
    codigo, nome = "sao_paulo", "Prefeitura de São Paulo (NFS-e)"


def obter(codigo):
    from .simulacao import Simulacao

    registro = {c.codigo: c for c in (Simulacao, Sefaz, NfseNacional, NfseSaoPaulo)}
    if codigo not in registro:
        raise ValidationError(f"Emissor desconhecido: {codigo}.")
    return registro[codigo]()
