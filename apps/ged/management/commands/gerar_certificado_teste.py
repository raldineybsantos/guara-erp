from django.core.management.base import BaseCommand, CommandError

from apps.core.validators import somente_digitos
from apps.ged.cert_teste import gerar_pfx


class Command(BaseCommand):
    help = "Gera um certificado A1 de TESTE (.pfx, não ICP-Brasil) para experimentar a assinatura digital."

    def add_arguments(self, parser):
        parser.add_argument("--nome", required=True)
        parser.add_argument("--cpf", default="")
        parser.add_argument("--cnpj", default="")
        parser.add_argument("--senha", default="teste123")
        parser.add_argument("--saida", default="certificado-teste.pfx")

    def handle(self, *args, nome, cpf, cnpj, senha, saida, **opts):
        cpf, cnpj = somente_digitos(cpf), somente_digitos(cnpj)
        if not (cpf or cnpj):
            raise CommandError("Informe --cpf ou --cnpj.")
        with open(saida, "wb") as f:
            f.write(gerar_pfx(nome, cpf=cpf, cnpj=cnpj, senha=senha))
        self.stdout.write(self.style.WARNING("CERTIFICADO DE TESTE — sem validade jurídica."))
        self.stdout.write(self.style.SUCCESS(f"Gerado: {saida} (senha: {senha})"))
