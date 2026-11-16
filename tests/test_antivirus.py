"""Antivírus: protocolo clamd (INSTREAM) contra um clamd simulado, falha fechada, limites, GED, XML e varredura."""
import io
import socket
import struct
import threading

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import LogAuditoria, Notificacao
from apps.ged.models import Documento, Pasta, Versao
from apps.seguranca import antivirus, varredura


class ClamdFalso(threading.Thread):
    """Mínimo do protocolo do clamd: PING, VERSION e INSTREAM (com limite de tamanho)."""

    def __init__(self, limite=10 * 1024 * 1024):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.porta = self.srv.getsockname()[1]
        self.modo = "normal"            # normal | erro | mudo
        self.limite = limite
        self.instreams = 0
        self.ativo = True

    def run(self):
        while self.ativo:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                self.atender(c)
            except OSError:
                pass
            finally:
                c.close()

    def ler(self, c, n):
        buf = b""
        while len(buf) < n:
            b = c.recv(n - len(buf))
            if not b:
                raise OSError("fechado")
            buf += b
        return buf

    def atender(self, c):
        cmd = b""
        while not cmd.endswith(b"\0"):
            cmd += self.ler(c, 1)
        if self.modo == "mudo":
            return
        if cmd == b"zPING\0":
            c.sendall(b"PONG\0")
        elif cmd == b"zVERSION\0":
            c.sendall(b"ClamAV 1.4.2/27000/Mon Nov 16 00:00:00 2026\0")
        elif cmd == b"zINSTREAM\0":
            self.instreams += 1
            dados, total = b"", 0
            while True:
                (n,) = struct.unpack("!I", self.ler(c, 4))
                if n == 0:
                    break
                total += n
                if total > self.limite:
                    c.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                    return
                dados += self.ler(c, n)
            if self.modo == "erro":
                c.sendall(b"Can't allocate memory ERROR\0")
            elif antivirus.eicar() in dados:
                c.sendall(b"stream: Eicar-Signature FOUND\0")
            else:
                c.sendall(b"stream: OK\0")


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        self.clamd = ClamdFalso()
        self.clamd.start()
        self.addCleanup(self.fechar)
        cfg = override_settings(ANTIVIRUS_ATIVO=True, CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=self.clamd.porta,
                                CLAMAV_SOCKET="", ANTIVIRUS_TIMEOUT=5, ANTIVIRUS_LIMITE_MB=100, MFA_ATIVO=False)
        cfg.enable()
        self.addCleanup(cfg.disable)
        self.adm = User.objects.create_superuser("adm", "a@x.com", "x")

    def fechar(self):
        self.derrubar()

    def derrubar(self):
        """Tira o servidor do ar de verdade: o accept() bloqueado precisa ser acordado para o socket fechar."""
        self.clamd.ativo = False
        try:
            self.clamd.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.clamd.join(2)
        self.clamd.srv.close()

    def arquivo(self, conteudo=b"ola mundo", nome="doc.txt"):
        return SimpleUploadedFile(nome, conteudo)


class ProtocoloTests(Base):
    def test_ping_versao_limpo_e_infectado(self):
        self.assertTrue(antivirus.ping())
        self.assertIn("ClamAV", antivirus.versao())
        self.assertEqual(antivirus.escanear(iter([b"limpo"])).situacao, "limpo")
        r = antivirus.escanear(iter([antivirus.eicar()]))
        self.assertEqual((r.situacao, r.assinatura), ("infectado", "Eicar-Signature"))

    def test_arquivo_grande_em_varios_blocos(self):
        dados = b"a" * (3 * 1024 * 1024) + antivirus.eicar() + b"b" * 1000
        self.assertEqual(antivirus.escanear(iter([dados])).situacao, "infectado")
        self.assertEqual(antivirus.escanear(iter([b"x" * 200_000, b"y" * 200_000])).situacao, "limpo")

    def test_eicar_e_o_padrao_da_industria(self):
        e = antivirus.eicar()
        self.assertEqual(len(e), 68)
        self.assertTrue(e.startswith(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR"))
        self.assertTrue(e.endswith(b"$H+H*"))

    def test_resposta_de_erro_nao_e_veredito(self):
        self.clamd.modo = "erro"
        with self.assertRaises(antivirus.AntivirusErro):
            antivirus.escanear(iter([b"x"]))
        self.clamd.modo = "mudo"
        with self.assertRaises(antivirus.AntivirusErro):
            antivirus.escanear(iter([b"x"]))

    def test_ping_falso_com_servidor_fora(self):
        self.derrubar()
        self.assertFalse(antivirus.ping())


class FalhaFechadaTests(Base):
    def test_arquivo_limpo_passa_e_nao_e_examinado_duas_vezes(self):
        f = self.arquivo()
        self.assertEqual(antivirus.garantir(f), "limpo")
        antivirus.garantir(f)
        self.assertEqual(self.clamd.instreams, 1)
        self.assertEqual(f.read(), b"ola mundo")            # o ponteiro volta ao início para ser salvo

    def test_ameaca_e_recusada_e_auditada(self):
        with self.assertRaisesMessage(ValidationError, "Eicar-Signature"):
            antivirus.garantir(self.arquivo(antivirus.eicar(), "x.txt"))
        self.assertTrue(LogAuditoria.objects.filter(modelo="seguranca.antivirus", descricao__contains="vírus Eicar").exists())

    def test_clamav_fora_do_ar_recusa_o_arquivo_e_avisa_so_uma_vez(self):
        self.derrubar()
        for _ in range(3):
            with self.assertRaisesMessage(ValidationError, "recusado por segurança"):
                antivirus.garantir(self.arquivo())
        self.assertEqual(Notificacao.objects.filter(usuario=self.adm, texto__startswith="Antivírus indisponível").count(), 1)
        self.assertTrue(LogAuditoria.objects.filter(modelo="seguranca.antivirus", descricao__contains="indisponível").exists())

    def test_erro_do_clamav_tambem_recusa(self):
        self.clamd.modo = "erro"
        with self.assertRaises(ValidationError):
            antivirus.garantir(self.arquivo())

    @override_settings(ANTIVIRUS_LIMITE_MB=1)
    def test_arquivo_acima_do_limite_e_recusado_sem_nem_consultar(self):
        with self.assertRaisesMessage(ValidationError, "limite de 1 MB"):
            antivirus.garantir(self.arquivo(b"x" * (1024 * 1024 + 1)))
        self.assertEqual(self.clamd.instreams, 0)
        antivirus.garantir(self.arquivo(b"x" * 1024))        # dentro do limite

    def test_limite_do_clamd_estourado_vira_recusa(self):
        self.clamd.limite = 1000                              # clamd configurado menor que o ERP
        with self.assertRaises(ValidationError):
            antivirus.garantir(self.arquivo(b"x" * 50_000))

    @override_settings(ANTIVIRUS_ATIVO=False)
    def test_desligado_nao_examina(self):
        self.derrubar()
        self.assertEqual(antivirus.garantir(self.arquivo(antivirus.eicar())), "nao_verificado")


class GedTests(Base):
    def setUp(self):
        super().setUp()
        self.doc = Documento.objects.create(pasta=Pasta.objects.create(nome="P"), titulo="D")

    def test_versao_limpa_fica_marcada(self):
        v = self.doc.adicionar_versao(self.arquivo(), usuario=self.adm)
        self.assertEqual(v.av_situacao, "limpo")
        self.assertIsNotNone(v.av_verificado_em)

    def test_ameaca_nao_cria_versao_nem_arquivo(self):
        with self.assertRaises(ValidationError):
            self.doc.adicionar_versao(self.arquivo(antivirus.eicar()), usuario=self.adm)
        self.assertEqual(Versao.objects.count(), 0)

    def test_clamav_fora_nao_cria_versao(self):
        self.derrubar()
        with self.assertRaises(ValidationError):
            self.doc.adicionar_versao(self.arquivo(), usuario=self.adm)
        self.assertEqual(Versao.objects.count(), 0)

    def test_tela_de_nova_versao_mostra_a_recusa_sem_erro_500(self):
        self.client.force_login(self.adm)
        r = self.client.post(reverse("ged:nova_versao", args=[self.doc.pk]),
                             {"arquivo": self.arquivo(antivirus.eicar(), "malware.txt")}, follow=True)
        self.assertContains(r, "Eicar-Signature")
        self.assertEqual(Versao.objects.count(), 0)
        self.derrubar()
        r = self.client.post(reverse("ged:nova_versao", args=[self.doc.pk]), {"arquivo": self.arquivo()}, follow=True)
        self.assertContains(r, "recusado por segurança")

    def test_conteudo_gerado_pelo_sistema_nao_depende_do_clamav(self):
        self.derrubar()
        cf = ContentFile(b"%PDF assinado", name="a.pdf")
        cf._antivirus_ok = True
        self.assertEqual(self.doc.adicionar_versao(cf, usuario=self.adm).av_situacao, "limpo")

    def test_download_de_arquivo_com_ameaca_e_bloqueado(self):
        v = self.doc.adicionar_versao(self.arquivo(), usuario=self.adm)
        self.client.force_login(self.adm)
        self.assertEqual(self.client.get(reverse("ged:download", args=[v.pk])).status_code, 200)
        Versao.objects.filter(pk=v.pk).update(av_situacao="infectado")
        r = self.client.get(reverse("ged:download", args=[v.pk]), follow=True)
        self.assertContains(r, "ameaça")
        self.assertTrue(LogAuditoria.objects.filter(descricao__startswith="Download BLOQUEADO").exists())

    def test_xml_da_nfe_infectado_e_recusado_antes_de_ler(self):
        from apps.fiscal.importador import importar_nfe

        with self.assertRaises(ValidationError):
            importar_nfe(self.arquivo(antivirus.eicar(), "nota.xml"))


class VarreduraTests(Base):
    def setUp(self):
        super().setUp()
        self.doc = Documento.objects.create(pasta=Pasta.objects.create(nome="P"), titulo="D")

    def antigos(self, *conteudos):
        """Versões 'de antes do antivírus': gravadas sem verificação."""
        with override_settings(ANTIVIRUS_ATIVO=False):
            return [self.doc.adicionar_versao(ContentFile(c, name=f"a{i}.txt"), usuario=self.adm) for i, c in enumerate(conteudos)]

    def test_varre_pendentes_marca_ameacas_bloqueia_e_avisa(self):
        limpo, sujo = self.antigos(b"ok", antivirus.eicar())
        r = varredura.varrer()
        self.assertEqual((r["limpo"], r["infectado"]), (1, 1))
        limpo.refresh_from_db()
        sujo.refresh_from_db()
        self.assertEqual((limpo.av_situacao, sujo.av_situacao), ("limpo", "infectado"))
        self.assertEqual(sujo.av_detalhe, "Eicar-Signature")
        self.assertTrue(Notificacao.objects.filter(usuario=self.adm, texto__startswith="Antivírus: 1 arquivo").exists())

    def test_clamav_fora_deixa_como_erro_e_nao_como_limpo(self):
        (v,) = self.antigos(b"ok")
        self.derrubar()
        r = varredura.varrer()
        v.refresh_from_db()
        self.assertEqual((v.av_situacao, r["erro"]), ("erro", 1))

    def test_arquivo_sumido_do_armazenamento(self):
        (v,) = self.antigos(b"ok")
        v.arquivo.storage.delete(v.arquivo.name)
        self.assertEqual(varredura.varrer()["ausente"], 1)

    def test_comando(self):
        self.antigos(b"ok")
        out = io.StringIO()
        call_command("antivirus_varrer", stdout=out)
        self.assertIn("Sem ameaças: 1", out.getvalue())
        self.derrubar()
        from django.core.management import CommandError

        with self.assertRaises(CommandError):
            call_command("antivirus_varrer")

    def test_rotina_diaria_avisa_quando_o_clamav_cai(self):
        self.derrubar()
        self.assertFalse(varredura.rotina_diaria()["no_ar"])
        self.assertTrue(Notificacao.objects.filter(texto__startswith="Antivírus indisponível").exists())


class TelaTests(Base):
    def test_painel_so_superusuario_e_botoes(self):
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        self.assertEqual(self.client.get(reverse("seguranca:antivirus")).status_code, 302)
        self.client.force_login(self.adm)
        r = self.client.get(reverse("seguranca:antivirus"))
        self.assertContains(r, "ClamAV respondendo")
        r = self.client.post(reverse("seguranca:antivirus_testar"), follow=True)
        self.assertContains(r, "EICAR foi detectado")
        self.clamd.modo = "erro"
        r = self.client.post(reverse("seguranca:antivirus_testar"), follow=True)
        self.assertContains(r, "não respondeu corretamente")

    def test_painel_avisa_quando_esta_fora(self):
        self.derrubar()
        self.client.force_login(self.adm)
        self.assertContains(self.client.get(reverse("seguranca:antivirus")), "ClamAV fora do ar")
