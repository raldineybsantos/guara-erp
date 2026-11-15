from django.core.management.base import BaseCommand, CommandError

from apps.core.validators import somente_digitos
from apps.fiscal.emissores.nfse_nacional import diagnosticar
from apps.fiscal.models import ConfiguracaoFiscal

SIMBOLO = {"ok": "[ ok ]", "aviso": "[ !! ]", "erro": "[ERRO]"}


class Command(BaseCommand):
    help = ("Confere se a empresa está pronta para emitir NFS-e pelo Emissor Nacional (cadastro, certificado, "
            "série). Com --conectar, testa o TLS mútuo com a Sefin sem emitir nada.")

    def add_arguments(self, parser):
        parser.add_argument("--cnpj", help="só esta empresa (padrão: todas com configuração fiscal)")
        parser.add_argument("--conectar", action="store_true", help="faz uma consulta à Sefin que não emite nota")

    def handle(self, *args, cnpj=None, conectar=False, **opts):
        configs = ConfiguracaoFiscal.objects.select_related("empresa")
        if cnpj:
            configs = configs.filter(empresa__cnpj__in=[cnpj, somente_digitos(cnpj)])
        if not configs.exists():
            raise CommandError("Nenhuma configuração fiscal encontrada. Crie em Fiscal → Configuração fiscal.")
        erros = 0
        for config in configs:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{config.empresa} ({config.empresa.cnpj})"))
            for nivel, texto in diagnosticar(config, conectar):
                erros += nivel == "erro"
                self.stdout.write(f"  {SIMBOLO[nivel]} {texto}")
        if erros:
            raise CommandError(f"{erros} pendência(s) a resolver antes de transmitir.")
