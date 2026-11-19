from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.bancario.cnab_cobranca import ler_retorno
from apps.bancario.models import ItemCobranca, RemessaCobranca, processar_retorno_cobranca
from apps.core.models import Cliente, Empresa
from apps.financeiro.models import ContaBancaria, ContaReceber


def linha(**campos):
    """Monta um registro de 240 posições colocando cada texto na posição (índice inicial) informada."""
    l = [" "] * 240
    for ini, txt in campos.items():
        i = int(ini[1:])
        l[i:i + len(txt)] = txt
    return "".join(l)


def retorno(*movs):
    """movs: (seu_numero, movimento, valor_titulo_centavos, valor_pago_centavos, data_credito DDMMAAAA)."""
    ls = [linha(p0="341" + "0000" + "0")]
    for n, (seu, mov, vt, vp, dt) in enumerate(movs, start=1):
        ls.append(linha(p0="3410001" + "3", p8=f"{2 * n - 1:05d}" + "T", p15=mov, p37="0000000001".ljust(20),
                        p58=seu.ljust(15), p81=f"{vt:015d}"))
        ls.append(linha(p0="3410001" + "3", p8=f"{2 * n:05d}" + "U", p15=mov, p77=f"{vp:015d}", p145=dt))
    ls.append(linha(p0="341" + "9999" + "9"))
    return "\r\n".join(ls) + "\r\n"


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.outro = User.objects.create_user("outro", password="x")
        emp = Empresa.objects.create(razao_social="Guará Móveis Ltda", cnpj="11222333000181")
        cls.conta = ContaBancaria.objects.create(
            nome="Itaú", banco="Itaú", codigo_banco="341", agencia="1234", conta="56789-0", empresa=emp,
            convenio_cobranca="CONV99", carteira="1")
        cls.cli = Cliente.objects.create(nome="Móveis São João Ltda", documento="11444777000161", cep="01310-100",
                                         logradouro="Av. Paulista", numero="1000", bairro="Bela Vista",
                                         cidade="São Paulo", uf="SP")
        cls.hoje = timezone.localdate()

    def cr(self, valor="1500.50", **kw):
        return ContaReceber.objects.create(cliente=self.cli, descricao="Venda", valor=D(valor), vencimento=self.hoje, **kw)


class RemessaTest(Base):
    def test_layout_e_nosso_numero(self):
        a, b = self.cr("1500.50"), self.cr("300")
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r.adicionar([a, b])
        itens = list(r.itens.all())
        self.assertEqual([i.nosso_numero for i in itens], ["0000000001", "0000000002"])
        self.assertNotIn(a, list(RemessaCobranca.elegiveis()))
        texto = r.gerar_arquivo(self.fin)
        linhas = texto.split("\r\n")[:-1]
        self.assertEqual([len(l) for l in linhas], [240] * 8)
        self.assertEqual([l[7] for l in linhas], ["0", "1", "3", "3", "3", "3", "5", "9"])
        p, q = linhas[2], linhas[3]
        self.assertEqual((p[13], q[13]), ("P", "Q"))
        self.assertEqual(p[37:57].strip(), "0000000001")
        self.assertEqual(p[62:77].strip(), f"RC{itens[0].pk}")
        self.assertEqual(int(p[85:100]), 150050)
        self.assertEqual((q[17], q[18:33]), ("2", "000011444777000161"[-15:]))
        self.assertIn("MOVEIS SAO JOAO LTDA", q)
        self.assertEqual((q[128:133], q[133:136], q[151:153]), ("01310", "100", "SP"))
        self.assertEqual(int(linhas[6][29:46]), 180050)
        self.assertEqual((r.status, r.sequencial), ("gerada", 1))
        a.refresh_from_db()
        self.assertEqual(a.status, "aberto")

    def test_nosso_numero_continua_entre_remessas(self):
        r1 = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r1.adicionar([self.cr()])
        r2 = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r2.adicionar([self.cr()])
        self.assertEqual(r2.itens.get().nosso_numero, "0000000002")

    def test_recusa_cadastro_incompleto(self):
        sem = Cliente.objects.create(nome="Sem Endereço", documento="52998224725")
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r.adicionar([ContaReceber.objects.create(cliente=sem, descricao="x", valor=D("10"), vencimento=self.hoje)])
        with self.assertRaisesMessage(ValidationError, "endereço do cliente incompleto"):
            r.gerar_arquivo()
        ContaBancaria.objects.filter(pk=self.conta.pk).update(convenio_cobranca="")
        r2 = RemessaCobranca.objects.create(conta_bancaria=ContaBancaria.objects.get(pk=self.conta.pk))
        r2.adicionar([self.cr()])
        with self.assertRaisesMessage(ValidationError, "convênio de cobrança"):
            r2.gerar_arquivo()

    def test_titulo_ja_pago_trava_geracao(self):
        a = self.cr("100")
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r.adicionar([a])
        a.baixar(D("100"), self.hoje, self.conta)
        with self.assertRaisesMessage(ValidationError, "já quitado"):
            r.gerar_arquivo()

    def test_nao_aceita_titulo_pago_ou_duplicado(self):
        a = self.cr("100")
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r.adicionar([a])
        with self.assertRaisesMessage(ValidationError, "outra cobrança"):
            r.adicionar([a])

    def test_cancelar_libera_titulos(self):
        a = self.cr("100")
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        r.adicionar([a])
        r.cancelar()
        self.assertIn(a, list(RemessaCobranca.elegiveis()))


class RetornoTest(Base):
    def remessa(self, *valores):
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        titulos = [self.cr(v) for v in valores]
        r.adicionar(titulos)
        r.gerar_arquivo()
        return r, titulos, list(r.itens.all())

    def test_ler_retorno_pares_t_u(self):
        regs = ler_retorno(retorno(("RC7", "06", 10000, 10050, "05112026")))
        self.assertEqual(regs[0]["seu_numero"], "RC7")
        self.assertEqual((regs[0]["valor_titulo"], regs[0]["valor_pago"]), (D("100.00"), D("100.50")))
        self.assertEqual(regs[0]["data_credito"].isoformat(), "2026-11-05")
        with self.assertRaises(ValidationError):
            ler_retorno("lixo\r\n")

    def test_registro_liquidacao_e_idempotencia(self):
        r, (t1, t2), (i1, i2) = self.remessa("100", "200")
        res = processar_retorno_cobranca(retorno((i1.seu_numero, "02", 10000, 0, "00000000"),
                                                 (i2.seu_numero, "02", 20000, 0, "00000000")))
        self.assertEqual(res["registrados"], 2)
        arq = retorno((i1.seu_numero, "06", 10000, 10000, "05112026"))
        res = processar_retorno_cobranca(arq)
        self.assertEqual(res["liquidados"], 1)
        t1.refresh_from_db(); i1.refresh_from_db()
        self.assertEqual((t1.status, t1.valor_pago, i1.status), ("pago", D("100.00"), "liquidado"))
        self.assertEqual(t1.data_pagamento.isoformat(), "2026-11-05")
        self.assertEqual(self.conta.saldo_atual, D("100.00"))
        self.assertEqual(processar_retorno_cobranca(arq)["ignorados"], 1)   # reprocesso não baixa duas vezes
        self.assertEqual(self.conta.saldo_atual, D("100.00"))
        r.refresh_from_db()
        self.assertEqual(r.status, "gerada")

    def test_pagamento_parcial_e_a_maior(self):
        r, (t1, t2), (i1, i2) = self.remessa("100", "200")
        res = processar_retorno_cobranca(retorno((i1.seu_numero, "06", 10000, 6000, "05112026"),
                                                 (i2.seu_numero, "06", 20000, 21000, "05112026")))
        t1.refresh_from_db(); t2.refresh_from_db()
        self.assertEqual((t1.status, t1.valor_pago), ("parcial", D("60.00")))
        self.assertEqual((t2.status, t2.valor_pago), ("pago", D("200.00")))
        self.assertTrue(any("maior que o saldo" in d for d in res["detalhes"]))
        r.refresh_from_db()
        self.assertEqual(r.status, "processada")

    def test_rejeicao_libera_titulo_e_baixa_do_banco(self):
        r, (t1, t2), (i1, i2) = self.remessa("100", "200")
        res = processar_retorno_cobranca(retorno((i1.seu_numero, "03", 10000, 0, "00000000"),
                                                 (i2.seu_numero, "09", 20000, 0, "00000000")))
        self.assertEqual((res["rejeitados"], res["baixados"]), (1, 1))
        t1.refresh_from_db()
        self.assertEqual(t1.status, "aberto")
        self.assertIn(t1, list(RemessaCobranca.elegiveis()))
        i1.refresh_from_db()
        self.assertEqual(i1.status, "rejeitado")

    def test_identificador_desconhecido_e_ignorado(self):
        self.remessa("100")
        self.assertEqual(processar_retorno_cobranca(retorno(("XX1", "06", 100, 100, "05112026")))["ignorados"], 1)


class TelasTest(Base):
    def test_fluxo(self):
        a = self.cr("100")
        self.client.force_login(self.fin)
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        self.assertContains(self.client.get(reverse("bancario:remessa_titulos", args=[r.pk])), "Venda")
        self.client.post(reverse("bancario:remessa_titulos", args=[r.pk]), {"contas": [a.pk]})
        self.assertEqual(r.itens.count(), 1)
        resp = self.client.post(reverse("bancario:remessa_arquivo", args=[r.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"RC", resp.content)
        self.assertEqual(self.client.get(reverse("bancario:remessacobranca_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("bancario:importar_retorno_cobranca")).status_code, 200)

    def test_sem_permissao(self):
        r = RemessaCobranca.objects.create(conta_bancaria=self.conta)
        self.client.force_login(self.outro)
        for nome in ("remessa_titulos", "remessa_arquivo", "remessa_cancelar"):
            self.assertEqual(self.client.get(reverse(f"bancario:{nome}", args=[r.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("bancario:importar_retorno_cobranca")).status_code, 403)
