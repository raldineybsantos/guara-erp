from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Cliente, Empresa
from apps.fiscal import transmissao
from apps.fiscal.models import ConfiguracaoFiscal, ItemNota, NotaFiscal, Produto
from apps.ged.cert_teste import gerar_pfx

MATRIZ, FILIAL, OUTRA = "11222333000181", "11222333000262", "11444777000161"


class MultiEmpresaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        fiscal = Group.objects.get(name="Fiscal")
        cls.ana, cls.bob = (User.objects.create_user(n, password="x") for n in ("ana", "bob"))
        cls.ana.groups.add(fiscal)
        cls.bob.groups.add(fiscal)
        cls.matriz = Empresa.objects.create(razao_social="Guará Matriz", cnpj=MATRIZ, uf="SP", regime_tributario="real")
        cls.filial = Empresa.objects.create(razao_social="Guará Filial RJ", cnpj=FILIAL, uf="RJ", matriz_de=cls.matriz)
        cls.outra = Empresa.objects.create(razao_social="Outra Empresa MEI", cnpj=OUTRA, uf="MG",
                                           regime_tributario="mei")
        cls.outra.usuarios.add(cls.bob)          # só o Bob trabalha na "Outra"
        cls.cli = Cliente.objects.create(nome="Cliente", documento="52998224725", tipo_pessoa="PF")
        cls.prod = Produto.objects.create(codigo="P", descricao="Mesa", ncm="94036000", cfop_padrao="5102", preco=10)
        cfg = ConfiguracaoFiscal.objects.create(empresa=cls.matriz, serie_nfe="1")
        cfg.guardar_certificado(gerar_pfx("GUARA", cnpj=MATRIZ, senha="s"), "s")
        ConfiguracaoFiscal.objects.create(empresa=cls.filial, serie_nfe="2", usar_certificado_matriz=True)
        ConfiguracaoFiscal.objects.create(empresa=cls.outra)

    def nota(self, empresa):
        n = NotaFiscal.objects.create(empresa=empresa, tipo="saida", modelo="55", cliente=self.cli)
        ItemNota.objects.create(nota=n, produto=self.prod, quantidade=1)
        n.recalcular()
        n.emitir()
        return n

    def test_acesso_e_filial(self):
        self.assertFalse(self.filial.matriz)
        self.assertEqual(set(Empresa.acessiveis(self.ana)), {self.matriz, self.filial})
        self.assertEqual(set(Empresa.acessiveis(self.bob)), {self.matriz, self.filial, self.outra})
        self.outra.ativa = False
        self.outra.save()
        self.assertNotIn(self.outra, Empresa.acessiveis(self.bob))

    def test_numeracao_serie_e_certificado_por_empresa(self):
        m1, m2, f1 = self.nota(self.matriz), self.nota(self.matriz), self.nota(self.filial)
        self.assertEqual([(n.serie, n.numero) for n in (m1, m2, f1)], [("1", 1), ("1", 2), ("2", 1)])
        t = transmissao.transmitir(f1, self.ana)           # filial assina com o certificado da matriz
        f1.refresh_from_db()
        self.assertEqual((t.resultado, f1.chave_acesso[:2], f1.chave_acesso[6:20]), ("autorizado", "33", FILIAL))
        o = self.nota(self.outra)
        with self.assertRaisesMessage(ValidationError, "não tem acesso"):
            transmissao.transmitir(o, self.ana)
        with self.assertRaisesMessage(ValidationError, "certificado A1 de Outra"):
            transmissao.transmitir(o, self.bob)             # tem acesso, mas a empresa não tem certificado
        with self.assertRaisesMessage(ValidationError, "outro CNPJ"):
            ConfiguracaoFiscal.objects.get(empresa=self.outra).guardar_certificado(
                gerar_pfx("GUARA", cnpj=MATRIZ, senha="s"), "s")

    def test_empresa_ativa_listas_e_formularios(self):
        nm, no = self.nota(self.matriz), self.nota(self.outra)
        self.client.force_login(self.ana)
        r = self.client.get(reverse("fiscal:notafiscal_list"))
        self.assertContains(r, "Guará Matriz")
        self.assertNotContains(r, "Outra Empresa MEI")
        self.assertEqual(self.client.get(no.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(nm.get_absolute_url()).status_code, 200)
        self.client.post(reverse("trocar_empresa"), {"empresa": self.outra.pk})     # sem acesso: ignora
        self.client.post(reverse("trocar_empresa"), {"empresa": self.filial.pk, "next": "//evil.example"})
        r = self.client.get(reverse("fiscal:notafiscal_create"))
        self.assertEqual(r.context["form"].initial["empresa"], self.filial.pk)
        self.assertNotIn(self.outra, r.context["form"].fields["empresa"].queryset)
        r = self.client.post(reverse("fiscal:notafiscal_create"), {
            "tipo": "saida", "modelo": "55", "serie": "1", "data_emissao": "2026-11-14", "natureza_operacao": "Venda",
            "cliente": self.cli.pk, "valor_frete": "0", "valor_desconto": "0",
            "itens-TOTAL_FORMS": "0", "itens-INITIAL_FORMS": "0"})
        self.assertEqual(r.status_code, 302, r.content[:1500])
        self.assertEqual(NotaFiscal.objects.latest("pk").empresa, self.filial)     # nasce na empresa ativa
        self.client.force_login(self.bob)
        self.assertContains(self.client.get(reverse("fiscal:notafiscal_list")), "Outra Empresa MEI")
        self.assertContains(self.client.get("/"), "Guará Filial RJ")               # seletor na barra
