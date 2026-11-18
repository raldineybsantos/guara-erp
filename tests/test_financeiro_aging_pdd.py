import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.contabilidade import servicos
from apps.contabilidade.models import ContaContabil
from apps.core.models import Cliente, Fornecedor
from apps.financeiro import aging
from apps.financeiro.models import ApuracaoPDD, ContaBancaria, ContaPagar, ContaReceber, FaixaPDD


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.hoje = timezone.localdate()
        cls.cli_a = Cliente.objects.create(nome="Alfa")
        cls.cli_b = Cliente.objects.create(nome="Beta")
        cls.banco = ContaBancaria.objects.create(nome="Banco")

    def receber(self, valor, atraso, cliente=None):
        return ContaReceber.objects.create(cliente=cliente or self.cli_a, descricao="Venda", valor=D(valor),
                                           vencimento=self.hoje - datetime.timedelta(days=atraso))

    def saldo(self, codigo):
        c = ContaContabil.objects.get(codigo=codigo)
        d, cr = servicos.saldos().get(c.pk, (D(0), D(0)))
        return servicos.saldo_natural(c, d, cr)


class AgingTest(Base):
    def test_faixas(self):
        casos = {-5: "a_vencer", 0: "a_vencer", 1: "f1", 30: "f1", 31: "f2", 60: "f2", 61: "f3", 90: "f3",
                 91: "f4", 180: "f4", 181: "f5", 900: "f5"}
        for atraso, chave in casos.items():
            self.assertEqual(aging.chave_faixa(atraso), chave, atraso)

    def test_aging_por_cliente_com_saldo_parcial(self):
        self.receber("100", -10)
        t = self.receber("200", 45)
        t.baixar(D("50"), self.hoje, self.banco)
        self.receber("300", 200, cliente=self.cli_b)
        self.receber("999", 10).cancelar()
        dados = aging.gerar(ContaReceber.objects.em_aberto(), self.hoje)
        por = {l["parceiro"].nome: l for l in dados["linhas"]}
        self.assertEqual((por["Alfa"]["a_vencer"], por["Alfa"]["f2"], por["Alfa"]["total"]),
                         (D("100"), D("150"), D("250")))
        self.assertEqual(por["Beta"]["f5"], D("300"))
        self.assertEqual(dados["totais"]["total"], D("550"))
        self.assertEqual(dados["linhas"][0]["parceiro"].nome, "Beta")     # maior saldo primeiro

    def test_tela_aging(self):
        self.client.force_login(self.fin)
        self.receber("100", 5)
        r = self.client.get(reverse("financeiro:aging"))
        self.assertContains(r, "Alfa")
        r = self.client.get(reverse("financeiro:aging"), {"tipo": "pagar"})
        self.assertEqual(r.status_code, 200)
        forn = Fornecedor.objects.create(nome="Forn")
        ContaPagar.objects.create(fornecedor=forn, descricao="c", valor=D("80"),
                                  vencimento=self.hoje - datetime.timedelta(days=40))
        self.assertContains(self.client.get(reverse("financeiro:aging"), {"tipo": "pagar"}), "Forn")

    def test_tela_sem_permissao(self):
        self.client.force_login(User.objects.create_user("x", password="x"))
        self.assertEqual(self.client.get(reverse("financeiro:aging")).status_code, 403)


class PDDTest(Base):
    def test_faixas_padrao(self):
        self.assertEqual(FaixaPDD.objects.count(), 6)

    def test_calculo_por_faixa(self):
        self.receber("1000", 10)     # 1%  -> 10
        self.receber("1000", 45)     # 5%  -> 50
        self.receber("1000", 400)    # 100% -> 1000
        self.receber("1000", -5)     # a vencer: fora
        calc = ApuracaoPDD.calcular(self.hoje)
        self.assertEqual((calc["base"], calc["necessaria"], calc["anterior"]), (D("3000"), D("1060"), D("0")))

    def test_apuracao_constitui_e_depois_reverte(self):
        t1, t2 = self.receber("1000", 400), self.receber("1000", 45)
        ap = ApuracaoPDD.apurar(self.hoje)
        self.assertEqual((ap.ajuste, ap.provisao_necessaria), (D("1050"), D("1050")))
        self.assertEqual(self.saldo("4.9"), D("1050"))
        self.assertEqual(self.saldo("1.1.4"), D("-1050"))       # redutora do ativo (natureza devedora)
        # o cliente do título de 400 dias paga: a necessidade cai para 50 e o ajuste é uma reversão
        t1.baixar(D("1000"), self.hoje, self.banco)
        ap2 = ApuracaoPDD.apurar(self.hoje)
        self.assertEqual((ap2.provisao_anterior, ap2.provisao_necessaria, ap2.ajuste), (D("1050"), D("50"), D("-1000")))
        self.assertEqual(self.saldo("4.9"), D("50"))
        # sem mudança: registra sem lançar
        ap3 = ApuracaoPDD.apurar(self.hoje)
        self.assertEqual(ap3.ajuste, 0)
        self.assertFalse(servicos.ativos_de(ap3).exists())

    def test_estorno_so_da_mais_recente(self):
        self.receber("1000", 45)
        a1 = ApuracaoPDD.apurar(self.hoje)
        self.receber("1000", 400)
        a2 = ApuracaoPDD.apurar(self.hoje)
        with self.assertRaises(ValidationError):
            a1.estornar()
        a2.estornar()
        self.assertEqual(ApuracaoPDD.constituida(a2.empresa), D("50"))
        self.assertEqual(self.saldo("4.9"), D("50"))
        a1.estornar()
        self.assertEqual(self.saldo("4.9"), D("0"))
        with self.assertRaises(ValidationError):
            a1.estornar()

    def test_apuracao_com_data_anterior_a_outra_recusada(self):
        self.receber("1000", 45)
        ApuracaoPDD.apurar(self.hoje)
        with self.assertRaises(ValidationError):
            ApuracaoPDD.apurar(self.hoje - datetime.timedelta(days=5))

    def test_titulo_renegociado_sai_da_base(self):
        t = self.receber("1000", 400)
        from apps.financeiro.models import Renegociacao
        Renegociacao.efetivar([t], self.hoje + datetime.timedelta(days=30))
        self.assertEqual(ApuracaoPDD.calcular(self.hoje)["necessaria"], D("0"))

    def test_telas(self):
        self.client.force_login(self.fin)
        self.receber("1000", 45)
        url = reverse("financeiro:pdd_apurar")
        self.assertContains(self.client.get(url), "Provisão já constituída")
        r = self.client.post(url, {"data": self.hoje.isoformat()})
        self.assertEqual(r.status_code, 302)
        ap = ApuracaoPDD.objects.get()
        self.assertEqual(self.client.get(ap.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:apuracaopdd_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:faixapdd_list")).status_code, 200)
        self.client.post(reverse("financeiro:pdd_estornar", args=[ap.pk]))
        ap.refresh_from_db()
        self.assertEqual(ap.status, "estornada")

    def test_percentual_invalido(self):
        f = FaixaPDD(nome="x", dias_minimos=999, percentual=D("101"))
        with self.assertRaises(ValidationError):
            f.full_clean()
