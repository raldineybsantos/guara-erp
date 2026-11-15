import csv
import datetime
import io
import logging
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core import auditoria
from apps.core.logs import registrar_erro
from apps.core.models import Colaborador, Empresa, LogAuditoria, LogErro, Notificacao, SeloAuditoria
from apps.estoque.models import Armazem


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "SenhaForte#2026")
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181")


class TrilhaTest(Base):
    def test_toda_alteracao_leva_usuario_ip_requisicao_e_empresa(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("estoque:armazem_create"),
                             {"empresa": self.emp.pk, "codigo": "09", "nome": "Almox Novo", "tipo": "normal",
                              "ativo": "on"}, REMOTE_ADDR="10.1.2.3", HTTP_USER_AGENT="NavegadorTeste/1.0")
        self.assertEqual(r.status_code, 302)
        log = LogAuditoria.objects.get(modelo="estoque.armazem", acao="criar")
        self.assertEqual((log.usuario_nome, log.ip, log.agente), ("admin", "10.1.2.3", "NavegadorTeste/1.0"))
        self.assertEqual(log.requisicao, r["X-Request-ID"])
        self.assertTrue(log.caminho.startswith("POST /estoque/"))
        self.assertEqual(log.empresa, self.emp)
        a = Armazem.objects.get(codigo="09")
        a.nome = "Renomeado"
        a.save()
        alt = LogAuditoria.objects.filter(modelo="estoque.armazem", acao="alterar").first()
        self.assertEqual(alt.alteracoes["nome"], ["Almox Novo", "Renomeado"])
        self.assertIn("nome: Almox Novo → Renomeado", alt.resumo_alteracoes)

    def test_modulos_novos_tambem_sao_auditados(self):
        from apps.crm.models import Oportunidade
        from apps.core.models import Cliente

        c = Cliente.objects.create(nome="Cli")
        Oportunidade.objects.create(cliente=c, titulo="Venda", valor=10)
        self.assertTrue(LogAuditoria.objects.filter(modelo="crm.oportunidade", acao="criar").exists())
        self.assertFalse(LogAuditoria.objects.filter(modelo__in=("core.notificacao", "core.logerro")).exists())

    def test_senha_so_aparece_como_impressao_digital(self):
        u = User.objects.create_user("maria", password="Primeira#2026")
        u.set_password("Segunda#2026")
        u.save()
        log = LogAuditoria.objects.filter(modelo="auth.user", objeto_id=str(u.pk), acao="alterar").first()
        antes, depois = log.alteracoes["password"]
        self.assertTrue(antes.startswith("***") and depois.startswith("***") and antes != depois)
        self.assertNotIn("pbkdf2", str(log.alteracoes))
        self.assertFalse(LogAuditoria.objects.filter(alteracoes__has_key="last_login").exists())

    def test_mudanca_de_grupo_e_permissao_fica_na_trilha(self):
        u = User.objects.create_user("joao", password="x")
        u.groups.add(Group.objects.get(name="Financeiro"))
        log = LogAuditoria.objects.filter(acao="seguranca", modelo="auth.user", objeto_id=str(u.pk)).first()
        self.assertIn("Financeiro", log.descricao)
        self.assertEqual(log.alteracoes["user_groups"]["itens"], ["Financeiro"])
        u.groups.clear()
        self.assertTrue(LogAuditoria.objects.filter(acao="seguranca", descricao__startswith="Removeu todos").exists())

    def test_acesso_negado_exportacao_e_leitura_sensivel(self):
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse("core:logerro_list")).status_code, 403)
        neg = LogAuditoria.objects.get(acao="negado")
        self.assertEqual((neg.usuario_nome, neg.modelo), ("comum", "auth.acesso"))
        self.client.force_login(self.admin)
        self.client.get(reverse("core:empresa_list") + "?export=csv")
        exp = LogAuditoria.objects.get(acao="exportar", modelo="core.empresa")
        self.assertIn("Exportou CSV", exp.descricao)
        col = Colaborador.objects.create(nome="Fulano", cpf="52998224725", matricula="1")
        self.client.get(reverse("core:colaborador_detail", args=[col.pk]))
        self.assertTrue(LogAuditoria.objects.filter(acao="ler", modelo="core.colaborador",
                                                    objeto_id=str(col.pk)).exists())


class ImutabilidadeTest(Base):
    def setUp(self):
        self.log = LogAuditoria.objects.create(acao="acao", modelo="x", descricao="original")

    def test_orm_recusa_alterar_e_apagar(self):
        self.log.descricao = "adulterado"
        with self.assertRaises(PermissionDenied):
            self.log.save()
        with self.assertRaises(PermissionDenied):
            self.log.delete()
        with self.assertRaises(PermissionDenied):
            LogAuditoria.objects.filter(pk=self.log.pk).update(descricao="x")
        with self.assertRaises(PermissionDenied):
            LogAuditoria.objects.all().delete()

    def test_banco_recusa_update_e_delete_diretos(self):
        for sql in ("UPDATE core_logauditoria SET descricao='x'", "DELETE FROM core_logauditoria"):
            with self.assertRaisesMessage(DatabaseError, "trilha de auditoria"), transaction.atomic():
                with connection.cursor() as c:
                    c.execute(sql)
        self.log.refresh_from_db()
        self.assertEqual(self.log.descricao, "original")

    def test_excluir_usuario_nao_perde_o_nome_nos_registros(self):
        u = User.objects.create_user("temporario", password="x")
        log = LogAuditoria.objects.create(usuario=u, acao="acao", modelo="x", descricao="fez algo")
        u.delete()   # o banco zera a chave estrangeira; é a única alteração que o gatilho permite
        log.refresh_from_db()
        self.assertEqual((log.usuario_id, log.usuario_nome), (None, "temporario"))
        self.assertTrue(auditoria.verificar()["ok"])


class SeloTest(Base):
    def adulterar(self, sql):
        with connection.cursor() as c:   # só o dono do banco consegue: derruba o gatilho de propósito
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")
            c.execute("ALTER TABLE core_logauditoria DISABLE TRIGGER auditoria_protegida")
            c.execute(sql)
            c.execute("ALTER TABLE core_logauditoria ENABLE TRIGGER auditoria_protegida")

    def test_cadeia_integra_e_detecta_adulteracao_e_remocao(self):
        for i in range(5):
            LogAuditoria.objects.create(acao="acao", modelo="x", descricao=f"evento {i}")
        s1 = auditoria.selar(forcar=True)
        LogAuditoria.objects.create(acao="acao", modelo="x", descricao="depois do primeiro selo")
        s2 = auditoria.selar(forcar=True)
        self.assertEqual(s2.anterior, s1.hash)
        self.assertTrue(auditoria.verificar()["ok"])
        alvo = LogAuditoria.objects.filter(descricao="evento 2").first()
        self.adulterar(f"UPDATE core_logauditoria SET descricao='evento FALSO' WHERE id={alvo.pk}")
        r = auditoria.verificar()
        self.assertFalse(r["ok"])
        self.assertTrue(any(f"Registro {alvo.pk}" in p for p in r["problemas"]), r["problemas"])
        self.adulterar(f"UPDATE core_logauditoria SET descricao='evento 2' WHERE id={alvo.pk}")
        self.assertTrue(auditoria.verificar()["ok"])
        self.adulterar(f"DELETE FROM core_logauditoria WHERE id={alvo.pk}")
        r = auditoria.verificar()
        self.assertFalse(r["ok"])
        self.assertTrue(any("apagado" in p or "não confere" in p for p in r["problemas"]), r["problemas"])

    def test_selo_nao_pode_ser_alterado(self):
        LogAuditoria.objects.create(acao="acao", modelo="x", descricao="a")
        s = auditoria.selar(forcar=True)
        with self.assertRaises(PermissionDenied):
            s.delete()
        with self.assertRaises(PermissionDenied):
            SeloAuditoria.objects.all().update(quantidade=9)
        with self.assertRaisesMessage(DatabaseError, "selos"), transaction.atomic():
            with connection.cursor() as c:
                c.execute("DELETE FROM core_seloauditoria")

    def test_margem_de_cinco_minutos_e_nada_novo(self):
        LogAuditoria.objects.create(acao="acao", modelo="x", descricao="recente")
        self.assertIsNone(auditoria.selar())    # ainda dentro da margem
        self.assertIsNotNone(auditoria.selar(agora=timezone.now() + datetime.timedelta(minutes=6)))
        self.assertIsNone(auditoria.selar(forcar=True))


class ErrosTest(Base):
    def falhar(self, texto="boom senha=abc123"):
        self.client.force_login(self.admin)
        with mock.patch("apps.core.views.render", side_effect=RuntimeError(texto)):
            self.client.raise_request_exception = False
            return self.client.get("/")

    def test_excecao_vira_log_com_codigo_na_pagina_500(self):
        r = self.falhar()
        self.assertEqual(r.status_code, 500)
        e = LogErro.objects.get()
        self.assertEqual((e.tipo, e.situacao, e.ocorrencias, e.usuario_nome), ("RuntimeError", "novo", 1, "admin"))
        self.assertNotIn("abc123", e.mensagem + e.detalhe)      # segredos não vão para o log
        self.assertIn("senha=***", e.mensagem)
        self.assertIn("Traceback", e.detalhe)
        self.assertEqual(e.requisicao, r["X-Request-ID"])
        self.assertContains(r, e.requisicao, status_code=500)   # o usuário vê o código para informar ao suporte
        self.assertTrue(Notificacao.objects.filter(usuario=self.admin, texto__contains="RuntimeError").exists())

    def test_erros_iguais_somam_e_resolvido_que_volta_e_reincidente(self):
        self.falhar()
        self.falhar()
        e = LogErro.objects.get()
        self.assertEqual(e.ocorrencias, 2)
        self.assertEqual(Notificacao.objects.filter(texto__contains="RuntimeError").count(), 1)   # só avisa na 1ª
        e.tratar("resolvido", self.admin, "corrigido")
        self.falhar()
        self.assertEqual(LogErro.objects.count(), 2)
        self.assertTrue(LogErro.objects.filter(situacao="novo").get().reincidente)

    def test_404_e_403_nao_viram_erro(self):
        self.client.force_login(self.admin)
        self.client.get("/nao-existe/")
        self.client.force_login(User.objects.create_user("x", password="x"))
        self.client.get(reverse("core:logerro_list"))
        self.assertFalse(LogErro.objects.exists())

    def test_logger_error_do_codigo_vai_para_o_banco_sem_duplicar_o_request(self):
        try:
            raise ValueError("falha ao importar")
        except ValueError:
            logging.getLogger("apps.bancario").exception("Importação falhou")
        e = LogErro.objects.get()
        self.assertEqual((e.origem, e.tipo), ("log", "ValueError"))
        logging.getLogger("django.request").error("Internal Server Error: /x")
        logging.getLogger("apps.x").warning("só aviso")
        self.assertEqual(LogErro.objects.count(), 1)

    def test_gravar_erro_nunca_levanta(self):
        with mock.patch("apps.core.models.LogErro.objects") as m:
            m.filter.side_effect = RuntimeError("banco fora")
            self.assertIsNone(registrar_erro(ValueError("x")))

    def test_tratamento_pela_tela_e_retencao(self):
        self.falhar()
        e = LogErro.objects.get()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("core:logerro_list")), "RuntimeError")
        self.assertContains(self.client.get(reverse("core:logerro_detail", args=[e.pk])), "Traceback")
        r = self.client.post(reverse("core:erro_resolver", args=[e.pk]), {"observacao": "causa corrigida"})
        self.assertEqual(r.status_code, 302)
        e.refresh_from_db()
        self.assertEqual((e.situacao, e.tratado_por, e.observacao), ("resolvido", self.admin, "causa corrigida"))
        LogErro.objects.filter(pk=e.pk).update(ultima_em=timezone.now() - datetime.timedelta(days=400))
        antigo_aberto = LogErro.objects.create(impressao="z", tipo="T", mensagem="m",
                                               ultima_em=timezone.now() - datetime.timedelta(days=400))
        r = auditoria.manutencao()
        self.assertEqual(r["erros_apagados"], 1)
        self.assertTrue(LogErro.objects.filter(pk=antigo_aberto.pk).exists())   # aberto nunca é apagado


class TelasEComandosTest(Base):
    def test_historico_do_registro_e_integridade_pela_tela(self):
        a = Armazem.objects.create(empresa=self.emp, codigo="H1", nome="Um")
        a.nome = "Dois"
        a.save()
        self.client.force_login(self.admin)
        r = self.client.get(reverse("core:historico", args=["estoque", "armazem", a.pk]))
        self.assertContains(r, "nome: Um → Dois")
        det = self.client.get(a.get_absolute_url() if hasattr(a, "get_absolute_url") else reverse("estoque:armazem_detail", args=[a.pk]))
        self.assertContains(det, "Histórico")
        self.assertEqual(self.client.get(reverse("core:historico", args=["core", "logauditoria", 1])).status_code, 404)
        r = self.client.post(reverse("core:auditoria_integridade"))
        self.assertContains(r, "Trilha íntegra")
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Verificou a integridade").exists())

    def test_grupo_auditoria_le_tudo_e_nao_altera(self):
        u = User.objects.create_user("aud", password="x")
        u.groups.add(Group.objects.get(name="Auditoria"))
        self.assertTrue(u.has_perm("core.view_logauditoria") and u.has_perm("core.view_logerro"))
        self.assertTrue(u.has_perm("financeiro.view_contapagar"))
        self.assertFalse(any(p.split(".")[1].startswith(("add_", "change_", "delete_"))
                             for p in u.get_all_permissions()))
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:logauditoria_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:logerro_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:auditoria_integridade")).status_code, 200)

    def test_comandos_verificar_e_exportar(self):
        LogAuditoria.objects.create(acao="acao", modelo="x", descricao="=cmd|' /C calc'!A0")
        out = io.StringIO()
        call_command("auditoria_verificar", stdout=out)
        self.assertIn("Trilha íntegra", out.getvalue())
        with tempfile.TemporaryDirectory() as d:
            alvo = Path(d) / "trilha.csv"
            call_command("auditoria_exportar", str(alvo), "--de", "2020-01-01", stdout=io.StringIO())
            linhas = list(csv.reader(alvo.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
            self.assertEqual(linhas[0][:3], ["id", "data_utc", "usuario"])
            self.assertTrue(any(l[7].startswith("'=cmd") for l in linhas[1:]))   # sem injeção de fórmula
            soma = (Path(d) / "trilha.csv.sha256").read_text().split()[0]
            import hashlib

            self.assertEqual(soma, hashlib.sha256(alvo.read_bytes()).hexdigest())
            with self.assertRaises(CommandError):
                call_command("auditoria_exportar", str(alvo), "--de", "ontem")
        self.assertTrue(LogAuditoria.objects.filter(acao="exportar", modelo="core.logauditoria").exists())

    def test_comando_detecta_adulteracao(self):
        LogAuditoria.objects.create(acao="acao", modelo="x", descricao="a")
        auditoria.selar(forcar=True)
        with connection.cursor() as c:
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")
            c.execute("ALTER TABLE core_logauditoria DISABLE TRIGGER auditoria_protegida")
            c.execute("UPDATE core_logauditoria SET descricao='b'")
            c.execute("ALTER TABLE core_logauditoria ENABLE TRIGGER auditoria_protegida")
        with self.assertRaises(CommandError):
            call_command("auditoria_verificar", stdout=io.StringIO(), stderr=io.StringIO())

    def test_rotina_diaria_sela_e_limpa(self):
        out = io.StringIO()
        call_command("rotinas_diarias", stdout=out)
        self.assertIn("Auditoria:", out.getvalue())
