"""Rotinas diárias dos módulos operacionais: OS preventivas e alertas de ASO/treinamentos vencendo.

Roda pelo serviço `agendador` (Docker) ou pelo Agendador de Tarefas (Windows, regua_windows.bat)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Abre OS preventivas que vencem e cria alertas de SST (ASO e reciclagem de NR)."

    def handle(self, *args, **options):
        from apps.manutencao.models import gerar_preventivas
        from apps.sst.models import alertar_vencimentos

        from apps.core import auditoria

        os_ = gerar_preventivas()
        sst = alertar_vencimentos()
        self.stdout.write(f"Manutenção: {os_} OS preventiva(s) aberta(s). SST: {sst} alerta(s).")
        from apps.rh import rotinas as rotinas_rh

        rh = rotinas_rh.executar()
        self.stdout.write(f"RH: {rh['periodos']} período(s) aquisitivo(s) criado(s), {rh['avisos']} aviso(s) de férias.")
        from apps.seguranca import backup

        self.stdout.write("Backup: " + ("ATRASADO — administradores avisados." if backup.verificar_atraso() else "em dia."))
        m = auditoria.manutencao()
        self.stdout.write(f"Auditoria: {'trilha selada' if m['selo'] else 'nada novo a selar'}; "
                          f"{m['erros_apagados']} erro(s) antigo(s) removido(s).")
