from django.core.management.base import BaseCommand, CommandError

from apps.core import auditoria


class Command(BaseCommand):
    help = "Confere a integridade da trilha de auditoria (hash de cada registro e selos encadeados)."

    def handle(self, *args, **options):
        r = auditoria.verificar()
        self.stdout.write(f"{r['registros']} registro(s), {r['selos']} selo(s), {r['sem_hash']} sem hash (antigos).")
        if not r["ok"]:
            for p in r["problemas"]:
                self.stderr.write(self.style.ERROR(p))
            raise CommandError(f"{len(r['problemas'])} inconsistência(s) na trilha de auditoria.")
        self.stdout.write(self.style.SUCCESS("Trilha íntegra."))
