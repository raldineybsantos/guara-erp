import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.bancario import cnab, ofx
from apps.bancario.models import Bordero, Extrato, processar_retorno
from apps.contabilidade.models import Lancamento
from apps.core.models import Empresa, Fornecedor
from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber, Movimentacao


def ofx_de(linhas):
    trn = "".join(f"<STMTTRN>\n<TRNTYPE>OTHER\n<DTPOSTED>{d:%Y%m%d}120000[-3:BRT]\n<TRNAMT>{v}\n<FITID>{f}\n"
                  f"<MEMO>{m}\n</STMTTRN>\n" for f, d, v, m in linhas)
    return f"OFXHEADER:100\nDATA:OFXSGML\n\n<OFX>\n<BANKTRANLIST>\n{trn}</BANKTRANLIST>\n</OFX>\n".encode("latin-1")


class BancarioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        emp = Empresa.objects.create(razao_social="Guará Móveis Ltda", cnpj="11222333000181")
        cls.conta = ContaBancaria.objects.create(nome="Itaú", banco="Itaú", codigo_banco="341", agencia="1234",
                                                 conta="56789-0", convenio="ABC123", empresa=emp)
        cls.forn = Fornecedor.objects.create(nome="Aço & Cia Ltda", documento="11444777000161", banco="237 Bradesco",
                                             agencia="0456-7", conta="12345-6")
        cls.hoje = timezone.localdate()

    def cp(self, valor, aprovado=True, **kw):
        return ContaPagar.objects.create(fornecedor=self.forn, descricao=f"NF {valor}", valor=valor,
                                         vencimento=self.hoje, aprovado=aprovado, **kw)

    # ------------------------------------------------------------- OFX e conciliação
    def test_ofx_valor_com_virgula_e_arquivo_invalido(self):
        l = ofx.ler(ofx_de([("a1", self.hoje, "-1.234,56", "TED"), ("a2", self.hoje, "10.00", "PIX")]))
        self.assertEqual([x["valor"] for x in l], [D("-1234.56"), D("10.00")])
        with self.assertRaisesMessage(ValidationError, "não é um extrato OFX"):
            ofx.ler(b"data;valor\n1;2")

    def test_conciliacao_automatica_titulo_avulsa_e_reimportacao(self):
        pago = self.cp(500)
        pago.baixar(500, self.hoje - datetime.timedelta(days=1), self.conta)   # já no ERP, banco mostra no dia seguinte
        aberto = ContaReceber.objects.create(descricao="Venda", valor=D("820.00"), vencimento=self.hoje)
        arq = ofx_de([("f1", self.hoje, "-500.00", "PAGTO FORNEC"), ("f2", self.hoje, "820.00", "PIX RECEBIDO"),
                      ("f3", self.hoje, "-12.90", "TARIFA PACOTE")])
        ext = Extrato.importar(self.conta, "nov.ofx", ofx.ler(arq))
        self.assertEqual((ext.linhas.count(), ext.pendentes()), (3, 2))
        self.assertTrue(Movimentacao.objects.get(contapagar=pago).conciliado)
        l2 = ext.linhas.get(fitid="f2")
        self.assertEqual(l2.titulos_sugeridos(), [aberto])
        l2.baixar_titulo(aberto)
        aberto.refresh_from_db()
        self.assertEqual(aberto.status, "pago")
        ext.linhas.get(fitid="f3").lancar_avulsa("Tarifa bancária")
        self.assertEqual((ext.pendentes(), ext.status), (0, "Conciliado"))
        self.assertTrue(Lancamento.objects.filter(evento="despesa_avulsa").exists())   # tarifa foi à contabilidade
        self.assertEqual(self.conta.saldo_atual, D("307.10"))
        with self.assertRaisesMessage(ValidationError, "já foram importados"):
            Extrato.importar(self.conta, "nov.ofx", ofx.ler(arq))
        ext2 = Extrato.importar(self.conta, "nov2.ofx", ofx.ler(arq + b"") if False else ofx.ler(
            ofx_de([("f3", self.hoje, "-12.90", "TARIFA"), ("f4", self.hoje, "-1.00", "IOF")])))
        self.assertEqual(ext2.linhas.count(), 1)

    # ------------------------------------------------------------- CNAB
    def test_remessa_layout_e_retorno_baixa_titulos(self):
        ted = self.cp(D("1500.50"))
        boleto = self.cp(D("300.00"), codigo_barras="34191.79001 01043.510047 91020.150008 5 99990000030000")
        nao_aprovado = self.cp(99, aprovado=False)
        b = Bordero.objects.create(conta_bancaria=self.conta, data_pagamento=self.hoje)
        self.assertNotIn(nao_aprovado, list(Bordero.elegiveis()))
        with self.assertRaisesMessage(ValidationError, "não está aprovado"):
            b.adicionar([nao_aprovado])
        b.adicionar([ted, boleto])
        self.assertNotIn(ted, list(Bordero.elegiveis()))   # preso neste borderô
        texto = b.gerar_remessa(self.fin)
        linhas = texto.split("\r\n")[:-1]
        self.assertEqual([len(l) for l in linhas], [240] * 8)
        self.assertEqual([l[7] for l in linhas], ["0", "1", "3", "5", "1", "3", "5", "9"])
        a, j = linhas[2], linhas[5]
        self.assertEqual((a[13], a[20:23], a[23:28], a[29:41], a[43:73].strip()), ("A", "237", "00456", "000000012345",
                                                                                   "ACO & CIA LTDA"))
        self.assertEqual(int(a[119:134]), 150050)
        self.assertEqual((j[13], j[17:61]), ("J", "34195999900000300001790001043510049102015000"))
        self.assertEqual(int(linhas[3][23:41]), 150050)
        self.assertEqual(linhas[0][0:3] + linhas[0][18:32], "34111222333000181")
        self.assertEqual((b.status, b.sequencial), ("remetido", 1))
        ted.refresh_from_db()
        self.assertEqual(ted.status, "aberto")   # só baixa com o retorno

        def retorno(oc_a, oc_j):
            r = list(linhas)
            r[2] = a[:154] + f"{self.hoje:%d%m%Y}" + "000000000150050" + a[177:230] + oc_a.ljust(10)
            r[5] = j[:230] + oc_j.ljust(10)
            return "\n".join(r)

        res = processar_retorno(retorno("BD", "CC"))
        self.assertEqual((res["agendados"], res["rejeitados"]), (1, 1))
        self.assertIn("Código de barras inválido", res["detalhes"][0])
        self.assertIn(boleto, list(Bordero.elegiveis()))   # rejeitado volta a ficar disponível
        res = processar_retorno(retorno("00", "CC"))
        ted.refresh_from_db()
        self.assertEqual((res["pagos"], ted.status, ted.forma_pagamento), (1, "pago", "ted"))
        res = processar_retorno(retorno("00", "CC"))   # mesmo arquivo de novo: não baixa duas vezes
        self.assertEqual((res["pagos"], Movimentacao.objects.filter(contapagar=ted).count()), (0, 1))
        b.refresh_from_db()
        self.assertEqual(b.status, "processado")
        with self.assertRaises(ValidationError):
            processar_retorno("lixo")

    def test_remessa_recusa_cadastro_incompleto(self):
        sem_banco = Fornecedor.objects.create(nome="Sem banco")
        c = ContaPagar.objects.create(fornecedor=sem_banco, descricao="x", valor=10, vencimento=self.hoje,
                                      aprovado=True)
        b = Bordero.objects.create(conta_bancaria=self.conta, data_pagamento=self.hoje)
        b.adicionar([c])
        with self.assertRaisesMessage(ValidationError, "dados bancários"):
            b.gerar_remessa()
        self.assertEqual(b.status, "aberto")
        with self.assertRaises(ValidationError):
            cnab.linha_para_barras("123")

    def test_telas(self):
        self.client.force_login(self.fin)
        r = self.client.post(reverse("bancario:importar_extrato"), {
            "conta_bancaria": self.conta.pk,
            "arquivo": SimpleUploadedFile("e.ofx", ofx_de([("x1", self.hoje, "-45.00", "TARIFA")]))})
        ext = Extrato.objects.get()
        self.assertRedirects(r, ext.get_absolute_url())
        self.assertContains(self.client.get(ext.get_absolute_url()), "TARIFA")
        linha = ext.linhas.get()
        self.client.post(reverse("bancario:resolver_linha", args=[linha.pk]), {"acao": "avulsa", "descricao": "Tarifa"})
        self.assertEqual(ext.pendentes(), 0)
        c = self.cp(100)
        r = self.client.post(reverse("bancario:bordero_create"), {"conta_bancaria": self.conta.pk,
                                                                  "data_pagamento": self.hoje})
        b = Bordero.objects.get()
        self.assertContains(self.client.get(reverse("bancario:bordero_titulos", args=[b.pk])), "NF 100")
        self.client.post(reverse("bancario:bordero_titulos", args=[b.pk]), {"contas": [c.pk]})
        r = self.client.post(reverse("bancario:bordero_remessa", args=[b.pk]))
        self.assertEqual((r.status_code, r["Content-Disposition"]), (200, f'attachment; filename="{b.numero}.rem"'))
        r = self.client.post(reverse("bancario:importar_retorno"),
                             {"arquivo": SimpleUploadedFile("r.ret", r.content)})
        self.assertEqual(r.status_code, 302)
        for n in ("extrato_list", "bordero_list", "importar_retorno", "importar_extrato"):
            self.assertEqual(self.client.get(reverse(f"bancario:{n}")).status_code, 200)
        comprador = User.objects.create_user("comp", password="x")
        comprador.groups.add(Group.objects.get(name="Compras"))
        self.client.force_login(comprador)
        self.assertEqual(self.client.get(reverse("bancario:bordero_list")).status_code, 403)
