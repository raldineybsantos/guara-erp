"""Backup: cifra, retenção, geração/verificação/restauração reais com pg_dump/pg_restore, cópia externa e telas."""
import datetime
import io
import os
import tempfile
from pathlib import Path
from unittest import mock

import psycopg
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import LogAuditoria, Notificacao
from apps.seguranca import backup
from apps.seguranca.models import RegistroBackup


class CifraTests(TestCase):
    def cifrar(self, dados, senha="frase-longa"):
        saida = io.BytesIO()
        c = backup._Cifrador(saida, senha)
        c.write(dados)
        c.close()
        return saida.getvalue()

    def abrir(self, blob, senha="frase-longa"):
        saida = io.BytesIO()
        backup.decifrar(io.BufferedReader(io.BytesIO(blob)), saida, senha)
        return saida.getvalue()

    def test_ida_e_volta_inclusive_varios_blocos(self):
        for tam in (0, 1, backup.BLOCO, backup.BLOCO + 1, 3 * backup.BLOCO + 7):
            dados = os.urandom(tam)
            self.assertEqual(self.abrir(self.cifrar(dados)), dados, tam)

    def test_senha_errada_e_adulteracao_sao_detectadas(self):
        blob = self.cifrar(b"segredo " * 1000)
        self.assertNotIn(b"segredo", blob)
        with self.assertRaises(backup.BackupErro):
            self.abrir(blob, "outra-senha")
        adulterado = bytearray(blob)
        adulterado[-20] ^= 1
        with self.assertRaises(backup.BackupErro):
            self.abrir(bytes(adulterado))

    def test_truncar_ou_acrescentar_e_detectado(self):
        dados = os.urandom(2 * backup.BLOCO + 5)
        blob = self.cifrar(dados)
        com_bloco_a_menos = blob[:len(blob) - 4 - 16 - 5]   # remove o bloco final inteiro
        with self.assertRaises(backup.BackupErro):
            self.abrir(com_bloco_a_menos)
        with self.assertRaises(backup.BackupErro):
            self.abrir(blob[:-10])
        with self.assertRaises(backup.BackupErro):
            self.abrir(blob + b"lixo")
        with self.assertRaises(backup.BackupErro):
            self.abrir(b"nao e um backup nenhum..........")


class RetencaoTests(TestCase):
    def nome(self, d):
        return f"guara-{d:%Y%m%d-%H%M%S}.gbk"

    def test_politica_avo_pai_filho(self):
        hoje = datetime.datetime(2026, 11, 16, 2, 0, 0)
        nomes = [self.nome(hoje - datetime.timedelta(days=i)) for i in range(0, 400)]
        nomes += [self.nome(hoje + datetime.timedelta(hours=3))]            # segundo backup do mesmo dia
        manter = backup.escolher_para_manter(nomes, 7, 4, 12)
        datas = sorted(backup._data_do_nome(n) for n in manter)
        dias = {d.date() for d in datas}
        for i in range(7):                                                   # últimos 7 dias presentes
            self.assertIn((hoje - datetime.timedelta(days=i)).date(), dias)
        self.assertIn(self.nome(hoje + datetime.timedelta(hours=3)), manter)  # do dia, fica o mais recente
        self.assertNotIn(self.nome(hoje), manter)
        meses = {(d.year, d.month) for d in datas}
        self.assertEqual(len([m for m in meses if m >= (2025, 12)]), 12)
        self.assertLess(len(manter), 30)
        self.assertNotIn(self.nome(hoje - datetime.timedelta(days=399)), manter)

    def test_ignora_arquivos_que_nao_sao_backup(self):
        self.assertEqual(backup.escolher_para_manter(["notas.txt", "guara-x.gbk"], 7, 4, 12), set())

    def test_aplicar_retencao_so_apaga_backups(self):
        with tempfile.TemporaryDirectory() as t:
            pasta = Path(t)
            hoje = datetime.datetime(2026, 11, 16, 2, 0, 0)
            for i in range(20):
                (pasta / self.nome(hoje - datetime.timedelta(days=i))).write_bytes(b"x")
            (pasta / "leia-me.txt").write_text("não mexa")
            with self.settings(BACKUP_RETENCAO_DIARIOS=3, BACKUP_RETENCAO_SEMANAIS=1, BACKUP_RETENCAO_MENSAIS=1):
                apagados = backup.aplicar_retencao(pasta)
            self.assertGreater(apagados, 10)
            self.assertTrue((pasta / "leia-me.txt").exists())
            restantes = [p for p in pasta.iterdir() if p.suffix == ".gbk"]
            self.assertGreaterEqual(len(restantes), 3)


def _ambiente(self):
    self._tmp = tempfile.TemporaryDirectory()
    raiz = Path(self._tmp.name)
    (raiz / "media").mkdir()
    (raiz / "media" / "ged").mkdir()
    (raiz / "media" / "ged" / "contrato.pdf").write_bytes(b"%PDF contrato de teste")
    cfg = override_settings(BACKUP_DIR=str(raiz / "bk"), MEDIA_ROOT=str(raiz / "media"), BACKUP_SENHA="frase-de-teste",
                            BACKUP_COPIA_DIR="", BACKUP_COMANDO_COPIA="", MFA_ATIVO=False)
    cfg.enable()
    self.addCleanup(cfg.disable)
    self.addCleanup(self._tmp.cleanup)
    self.raiz = raiz


class GerarVerificarTests(TransactionTestCase):
    def setUp(self):
        _ambiente(self)

    def test_gerar_verificar_e_detectar_adulteracao(self):
        User.objects.create_user("backup-teste", password="x")
        r = backup.gerar()
        self.assertTrue(r["caminho"].exists())
        self.assertEqual(r["arquivos_media"], 1)
        self.assertEqual(oct(r["caminho"].stat().st_mode & 0o777), "0o600")
        self.assertNotIn(b"PostgreSQL", r["caminho"].read_bytes()[:2000])      # nada legível: está cifrado
        m = backup.verificar(r["caminho"])
        self.assertEqual(m["arquivos_media"], 1)
        with self.assertRaises(backup.BackupErro):
            backup.verificar(r["caminho"], senha="errada")
        dados = bytearray(r["caminho"].read_bytes())
        dados[len(dados) // 2] ^= 0xFF
        r["caminho"].write_bytes(bytes(dados))
        with self.assertRaises(backup.BackupErro):
            backup.verificar(r["caminho"])

    def test_restaurar_em_outro_banco_devolve_dados_e_arquivos(self):
        User.objects.create_user("ana-restaurar", password="x")
        r = backup.gerar()
        cfg = connection.settings_dict
        base = dict(host=cfg["HOST"], port=cfg["PORT"], user=cfg["USER"], password=cfg["PASSWORD"])
        with psycopg.connect(dbname=cfg["NAME"], autocommit=True, **base) as c:
            c.execute("DROP DATABASE IF EXISTS guara_restore_teste")
            c.execute("CREATE DATABASE guara_restore_teste")
        try:
            (self.raiz / "media" / "ged" / "contrato.pdf").unlink()           # "perde" o arquivo
            m = backup.restaurar(r["caminho"], banco_destino="guara_restore_teste")
            self.assertEqual(m["arquivos_restaurados"], 1)
            self.assertEqual((self.raiz / "media" / "ged" / "contrato.pdf").read_bytes(), b"%PDF contrato de teste")
            with psycopg.connect(dbname="guara_restore_teste", **base) as c:
                n = c.execute("SELECT count(*) FROM auth_user WHERE username='ana-restaurar'").fetchone()[0]
            self.assertEqual(n, 1)
        finally:
            with psycopg.connect(dbname=cfg["NAME"], autocommit=True, **base) as c:
                c.execute("DROP DATABASE IF EXISTS guara_restore_teste WITH (FORCE)")

    def test_comando_restaurar_exige_confirmacao(self):
        from django.core.management import CommandError

        r = backup.gerar()
        with self.assertRaises(CommandError):
            call_command("backup_restaurar", str(r["caminho"]))
        with self.assertRaises(CommandError):
            call_command("backup_restaurar", str(r["caminho"]), "--confirmo", "banco-errado")

    def test_backup_nao_inclui_a_propria_pasta_de_backups(self):
        with override_settings(MEDIA_ROOT=str(self.raiz), BACKUP_DIR=str(self.raiz / "bk")):
            backup.gerar()
            r = backup.gerar()
            # com a pasta de backups dentro do MEDIA_ROOT, só o PDF entra: nenhum .gbk antigo
            self.assertEqual(r["arquivos_media"], 1)
            with tempfile.TemporaryDirectory() as t:
                m = backup.abrir(r["caminho"], Path(t))
                nomes = [p.name for p in Path(t).rglob("*") if p.is_file()]
                self.assertNotIn(".gbk", " ".join(nomes))

    def test_executar_registra_copia_retencao_e_auditoria(self):
        copia = self.raiz / "nas"
        with override_settings(BACKUP_COPIA_DIR=str(copia)):
            reg = backup.executar("manual")
        self.assertEqual(reg.situacao, "ok", reg.erro)
        self.assertTrue(reg.verificado)
        self.assertTrue((copia / reg.arquivo).exists())
        self.assertIn("pasta", reg.copia_externa)
        self.assertEqual(reg.sha256, backup._sha256(self.raiz / "bk" / reg.arquivo))
        self.assertTrue(LogAuditoria.objects.filter(modelo="seguranca.backup", descricao__contains="concluído").exists())

    def test_comando_de_copia_externa(self):
        destino = self.raiz / "remoto"
        destino.mkdir()
        with override_settings(BACKUP_COMANDO_COPIA=f"cp {{arquivo}} {destino}"):
            reg = backup.executar()
        self.assertEqual(reg.situacao, "ok", reg.erro)
        self.assertTrue((destino / reg.arquivo).exists())

    def test_falha_da_copia_externa_nao_perde_o_backup_local_e_avisa(self):
        User.objects.create_superuser("adm", "a@x.com", "x")
        with override_settings(BACKUP_COMANDO_COPIA="false"):
            reg = backup.executar()
        self.assertEqual(reg.situacao, "ok")
        self.assertIn("cópia externa falhou", reg.erro)
        self.assertTrue((self.raiz / "bk" / reg.arquivo).exists())
        self.assertTrue(Notificacao.objects.filter(texto__startswith="Backup com problema").exists())

    def test_falha_do_pg_dump_gera_registro_de_erro_e_aviso(self):
        User.objects.create_superuser("adm", "a@x.com", "x")
        with mock.patch("apps.seguranca.backup.shutil.which", return_value=None):
            reg = backup.executar()
        self.assertEqual(reg.situacao, "erro")
        self.assertIn("pg_dump", reg.erro)
        self.assertTrue(Notificacao.objects.filter(texto__startswith="Backup com problema").exists())
        self.assertEqual(list((self.raiz / "bk").glob("*.gbk")) + list((self.raiz / "bk").glob("*.parcial")), [])

    def test_alerta_de_backup_atrasado(self):
        User.objects.create_superuser("adm", "a@x.com", "x")
        self.assertTrue(backup.verificar_atraso())            # nunca houve backup
        self.assertEqual(Notificacao.objects.filter(texto__startswith="Nenhum backup").count(), 1)
        backup.verificar_atraso()                              # no mesmo dia não repete
        self.assertEqual(Notificacao.objects.filter(texto__startswith="Nenhum backup").count(), 1)
        RegistroBackup.objects.create(situacao="ok")
        self.assertFalse(backup.verificar_atraso())


class BackupTelasTests(TransactionTestCase):
    def setUp(self):
        _ambiente(self)
        self.adm = User.objects.create_superuser("adm", "a@x.com", "x")
        self.comum = User.objects.create_user("comum", password="x")

    def test_somente_superusuario(self):
        self.client.force_login(self.comum)
        self.assertEqual(self.client.get(reverse("seguranca:backups")).status_code, 302)
        self.assertEqual(self.client.post(reverse("seguranca:backup_executar")).status_code, 302)
        self.client.force_login(self.adm)
        r = self.client.get(reverse("seguranca:backups"))
        self.assertEqual(r.status_code, 200)

    def test_baixar_e_verificar_e_nome_invalido(self):
        reg = backup.executar()
        self.client.force_login(self.adm)
        r = self.client.get(reverse("seguranca:backup_baixar", args=[reg.arquivo]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Download do backup").exists())
        r = self.client.post(reverse("seguranca:backup_verificar", args=[reg.arquivo]), follow=True)
        self.assertContains(r, "está íntegro")
        self.assertEqual(self.client.get("/seguranca/backups/..%2F..%2Fetc%2Fpasswd/baixar/").status_code, 404)
        self.assertEqual(self.client.get("/seguranca/backups/guara-20260101-000000.gbk/baixar/").status_code, 404)
        self.client.force_login(self.comum)
        self.assertEqual(self.client.get(reverse("seguranca:backup_baixar", args=[reg.arquivo])).status_code, 302)

    def test_fazer_backup_agora_dispara_em_segundo_plano(self):
        self.client.force_login(self.adm)
        with mock.patch("apps.seguranca.views_backup.threading.Thread") as T:
            r = self.client.post(reverse("seguranca:backup_executar"), follow=True)
        T.assert_called_once()
        self.assertContains(r, "Backup iniciado")
