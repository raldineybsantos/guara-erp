"""Agendador simples: executa a régua de cobrança uma vez por dia no horário definido.

Uso: python manage.py agendador --hora 08:00
(No Docker roda no serviço `agendador`; no Windows pode-se usar o Agendador de Tarefas com executar_regua.)
"""
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.cobranca.models import ReguaCobranca


class Command(BaseCommand):
    help = "Rotinas diárias no horário informado: régua de cobrança e vencimento/renovação de contratos."

    def add_arguments(self, parser):
        parser.add_argument("--hora", default="08:00", help="Horário HH:MM (padrão 08:00)")
        parser.add_argument("--hora-backup", default=None, help="Horário do backup diário (padrão: BACKUP_HORA, 02:00)")

    def handle(self, *args, hora, hora_backup=None, **options):
        from django.conf import settings

        alvo = hora.strip()
        alvo_backup = (hora_backup or settings.BACKUP_HORA).strip()
        ultimo_backup = None
        ultimo_gateway = None
        ultimo_dia = None
        ultimo_sla = None
        self.stdout.write(f"Agendador ativo: régua de cobrança diariamente às {alvo}; backup às {alvo_backup}.")
        while True:
            agora = timezone.localtime()
            if agora.strftime("%H:%M") >= alvo and ultimo_dia != agora.date():
                try:
                    n = ReguaCobranca.executar()
                    self.stdout.write(f"{agora:%d/%m/%Y %H:%M} régua executada: {n} envio(s).")
                except Exception as exc:  # mantém o agendador vivo
                    self.stderr.write(f"Erro na régua: {exc}")
                try:
                    from apps.juridico.models import processar_vencimentos
                    alertas, renovados = processar_vencimentos()
                    self.stdout.write(f"Contratos: {alertas} alerta(s), {renovados} renovação(ões) automática(s).")
                except Exception as exc:
                    self.stderr.write(f"Erro nos contratos: {exc}")
                try:
                    from django.core.management import call_command
                    call_command("rotinas_diarias", stdout=self.stdout)
                except Exception as exc:
                    self.stderr.write(f"Erro nas rotinas de manutenção/SST: {exc}")
                ultimo_dia = agora.date()
            if agora.strftime("%H:%M") >= alvo_backup and ultimo_backup != agora.date():
                ultimo_backup = agora.date()   # uma tentativa por dia; a falha gera aviso, não repetição em laço
                try:
                    from apps.seguranca import backup
                    reg = backup.executar("agendado")
                    self.stdout.write(f"{agora:%d/%m/%Y %H:%M} backup: {reg.get_situacao_display()} {reg.arquivo} {reg.erro}")
                except Exception as exc:
                    self.stderr.write(f"Erro no backup: {exc}")
            if ultimo_gateway is None or (agora - ultimo_gateway).total_seconds() >= 3600:
                try:  # a cada hora: rede de segurança para avisos de pagamento perdidos
                    from apps.cobranca.gateway import servico as gateway
                    r = gateway.sincronizar()
                    if r["baixadas"] or r["canceladas"]:
                        self.stdout.write(f"{agora:%H:%M} Gateway: {r['baixadas']} baixa(s), {r['canceladas']} cancelamento(s).")
                except Exception as exc:
                    self.stderr.write(f"Erro na conciliação do gateway: {exc}")
                ultimo_gateway = agora
            if ultimo_sla is None or (agora - ultimo_sla).total_seconds() >= 900:
                try:  # a cada 15 minutos: avisos de SLA do Service Desk
                    from apps.servicedesk.models import verificar_slas
                    avisos, fechados = verificar_slas()
                    if avisos or fechados:
                        self.stdout.write(f"{agora:%H:%M} Service Desk: {avisos} aviso(s) de SLA, {fechados} fechado(s).")
                except Exception as exc:
                    self.stderr.write(f"Erro no SLA do Service Desk: {exc}")
                ultimo_sla = agora
            time.sleep(30)
