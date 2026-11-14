import datetime
import shutil
import subprocess
import tempfile

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Cliente, Empresa, LogAuditoria
from apps.fiscal import assinatura_xml, chave, transmissao
from apps.fiscal.models import ConfiguracaoFiscal, ItemNota, NotaFiscal, Produto
from apps.ged.assinatura import carregar_pfx
from apps.ged.cert_teste import gerar_pfx

CNPJ = "11222333000181"


class TransmissaoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fiscal = User.objects.create_user("fiscal", password="x")
        cls.fiscal.groups.add(Group.objects.get(name="Fiscal"))
        cls.emp = Empresa.objects.create(razao_social="Guará Ltda", cnpj=CNPJ, uf="SP", matriz=True)
        cls.cli = Cliente.objects.create(nome="Cliente", documento="11444777000161")
        cls.prod = Produto.objects.create(codigo="P", descricao="Mesa", ncm="94036000", cfop_padrao="5102", preco=100)
        cls.pfx = gerar_pfx("GUARA LTDA", cnpj=CNPJ, senha="s3nha")

    def nota(self, modelo="55", **kw):
        n = NotaFiscal.objects.create(empresa=self.emp, tipo="saida", modelo=modelo, cliente=self.cli, **kw)
        ItemNota.objects.create(nota=n, produto=self.prod, quantidade=2)
        n.recalcular()
        n.emitir()
        return n

    def config(self, **kw):
        c = ConfiguracaoFiscal.objects.create(empresa=self.emp, **kw)
        c.guardar_certificado(self.pfx, "s3nha")
        return c

    def test_chave_de_acesso(self):
        # exemplo público do Manual de Orientação do Contribuinte: DV calculado por módulo 11
        self.assertEqual(chave.digito("5206043300991100250655012000000780026730161"), 5)
        ch = chave.gerar("SP", datetime.date(2026, 11, 14), CNPJ, "55", "1", 123, codigo=7)
        self.assertEqual((len(ch), ch[:2], ch[2:6], ch[6:20], ch[20:22], ch[25:34]), (44, "35", "2611", CNPJ, "55",
                                                                                     "000000123"))
        self.assertTrue(chave.valida(ch))
        self.assertFalse(chave.valida(ch[:-1] + str((int(ch[-1]) + 1) % 10)))

    def test_assinatura_xml_confere_e_detecta_adulteracao(self):
        chave_priv, cert, _, _ = carregar_pfx(self.pfx, "s3nha")
        xml = b'<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe Id="NFe123"><v>10.00</v></infNFe></NFe>'
        for alg in ("sha1", "sha256"):
            assinado = assinatura_xml.assinar(xml, "NFe123", chave_priv, cert, alg)
            self.assertTrue(assinatura_xml.verificar(assinado))
            self.assertFalse(assinatura_xml.verificar(assinado.replace(b"10.00", b"99.00")))
        if shutil.which("xmlsec1"):   # confere com uma implementação independente, quando disponível
            with tempfile.NamedTemporaryFile(suffix=".xml") as f:
                f.write(assinado)
                f.flush()
                r = subprocess.run(["xmlsec1", "--verify", "--insecure", "--id-attr:Id", "infNFe", f.name],
                                   capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_cofre_valida_cnpj_e_nao_guarda_em_claro(self):
        c = ConfiguracaoFiscal.objects.create(empresa=self.emp)
        with self.assertRaisesMessage(ValidationError, "outro CNPJ"):
            c.guardar_certificado(gerar_pfx("OUTRA", cnpj="11444777000161", senha="x"), "x")
        with self.assertRaisesMessage(ValidationError, "senha incorreta"):
            c.guardar_certificado(self.pfx, "errada")
        with self.assertRaisesMessage(ValidationError, "e-CNPJ"):
            c.guardar_certificado(gerar_pfx("Pessoa", cpf="52998224725", senha="x"), "x")
        c.guardar_certificado(self.pfx, "s3nha")
        c.refresh_from_db()
        self.assertNotIn(b"s3nha", bytes(c.senha))
        self.assertNotEqual(bytes(c.certificado), self.pfx)
        self.assertEqual(c.status, "Vigente")
        c.abrir_certificado()
        with self.settings(FISCAL_CHAVE_COFRE="outra-chave"):
            with self.assertRaisesMessage(ValidationError, "chave do cofre mudou"):
                c.abrir_certificado()
        self.assertFalse(LogAuditoria.objects.filter(descricao__contains="gAAAA").exists())

    def test_simulacao_autoriza_rejeita_e_cancela(self):
        self.config()
        n = self.nota()
        t = transmissao.transmitir(n, self.fiscal)
        n.refresh_from_db()
        self.assertEqual((t.resultado, n.situacao_fiscal, t.ambiente), ("autorizado", "autorizada", "homologacao"))
        self.assertTrue(chave.valida(n.chave_acesso))
        self.assertTrue(assinatura_xml.verificar(t.xml_enviado.encode()))
        self.assertIn("SIMULAÇÃO", t.mensagem)
        with self.assertRaises(ValidationError):
            transmissao.transmitir(n)
        with self.assertRaisesMessage(ValidationError, "15 caracteres"):
            transmissao.cancelar(n, "curta")
        transmissao.cancelar(n, "Erro no valor do frete informado", self.fiscal)
        n.refresh_from_db()
        self.assertEqual((n.status, n.situacao_fiscal), ("cancelada", "cancelada"))
        sem_ncm = self.nota()
        sem_ncm.itens.update(ncm="")
        t = transmissao.transmitir(sem_ncm)
        sem_ncm.refresh_from_db()
        self.assertEqual((t.resultado, sem_ncm.situacao_fiscal), ("rejeitado", "rejeitada"))
        self.assertIn("NCM", t.mensagem)
        sem_ncm.itens.update(ncm="94036000")
        self.assertEqual(transmissao.transmitir(sem_ncm).resultado, "autorizado")   # corrigiu e reenviou

    def test_emissores_reais_recusam_sem_fingir(self):
        self.config(emissor_nfe="sefaz", emissor_nfse="sao_paulo")
        for modelo in ("55", "nfse"):
            n = self.nota(modelo)
            with self.assertRaisesMessage(ValidationError, "ainda não está liberada"):
                transmissao.transmitir(n)
            n.refresh_from_db()
            self.assertEqual((n.situacao_fiscal, n.transmissoes.count()), ("", 0))
        ConfiguracaoFiscal.objects.update(emissor_nfse="simulacao")
        self.assertEqual(transmissao.transmitir(self.nota("nfse")).resultado, "autorizado")

    def test_telas(self):
        self.client.force_login(self.fiscal)
        r = self.client.post(reverse("fiscal:configuracaofiscal_create"), {
            "empresa": self.emp.pk, "ambiente": "homologacao", "emissor_nfe": "simulacao", "emissor_nfse": "simulacao"})
        c = ConfiguracaoFiscal.objects.get()
        n = self.nota()
        self.client.post(reverse("fiscal:transmitir_nota", args=[n.pk]))
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "")   # sem certificado não transmite
        r = self.client.post(reverse("fiscal:enviar_certificado", args=[c.pk]),
                             {"arquivo": SimpleUploadedFile("a1.pfx", self.pfx), "senha": "s3nha"})
        self.assertRedirects(r, c.get_absolute_url())
        self.assertContains(self.client.get(c.get_absolute_url()), "GUARA LTDA")
        self.client.post(reverse("fiscal:transmitir_nota", args=[n.pk]))
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "autorizada")
        self.assertContains(self.client.get(n.get_absolute_url()), "SIM-100")
        self.client.post(reverse("fiscal:cancelar_nota", args=[n.pk]))
        n.refresh_from_db()
        self.assertEqual(n.status, "emitida")   # autorizada só cancela pelo fisco
        self.client.post(reverse("fiscal:cancelar_fiscal", args=[n.pk]), {"justificativa": "Cliente desistiu da compra"})
        n.refresh_from_db()
        self.assertEqual(n.status, "cancelada")
        vend = User.objects.create_user("vend", password="x")
        vend.groups.add(Group.objects.get(name="Vendas"))
        self.client.force_login(vend)
        self.assertEqual(self.client.get(reverse("fiscal:enviar_certificado", args=[c.pk])).status_code, 403)
