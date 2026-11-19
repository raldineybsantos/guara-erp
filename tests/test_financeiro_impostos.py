import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Fornecedor
from apps.financeiro.borderoimposto import BorderoImposto
from apps.financeiro.models import (ContaBancaria, ContaPagar, GuiaTributo, Movimentacao, RegraRetencao, Retencao,
                                    valor_retencao)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.banco = ContaBancaria.objects.create(nome="Banco")
        cls.forn = Fornecedor.objects.create(nome="Serviços Ltda")
        cls.hoje = timezone.localdate()

    def guia(self, valor="1000", dias=0, tributo="IRRF", codigo="1708"):
        return GuiaTributo.objects.create(tributo=tributo, codigo_receita=codigo, valor=D(valor),
                                          competencia=self.hoje.replace(day=1),
                                          vencimento=self.hoje - datetime.timedelta(days=dias))


class EncargosTest(Base):
    def test_em_dia_sem_encargos(self):
        self.assertEqual(self.guia().calcular_encargos(self.hoje), (D("0"), D("0")))

    def test_multa_diaria_e_juros(self):
        g = self.guia("1000", dias=10)
        self.assertEqual(g.calcular_encargos(self.hoje, D("2")), (D("33.00"), D("30.00")))

    def test_multa_limitada_a_20_por_cento(self):
        g = self.guia("1000", dias=200)
        self.assertEqual(g.calcular_encargos(self.hoje)[0], D("200.00"))

    def test_recolher_com_encargos_gera_duas_saidas_e_estorna(self):
        g = self.guia("1000", dias=10)
        g.recolher(self.banco, self.hoje, D("33"), D("10"))
        g.refresh_from_db()
        self.assertEqual((g.status, g.total), ("paga", D("1043.00")))
        self.assertEqual(Movimentacao.objects.filter(conta_bancaria=self.banco).count(), 2)
        self.assertEqual(self.banco.saldo_atual, D("-1043.00"))
        g.estornar_recolhimento()
        g.refresh_from_db()
        self.assertEqual((g.status, g.multa, g.juros), ("aberta", 0, 0))
        self.assertFalse(Movimentacao.objects.exists())

    def test_encargo_negativo_rejeitado(self):
        with self.assertRaises(ValidationError):
            self.guia().recolher(self.banco, self.hoje, D("-1"), 0)


class TetoTest(Base):
    def test_teto_mensal_limita_retencao_por_fornecedor(self):
        regra = RegraRetencao.objects.get(tributo="IRRF")
        regra.teto_mensal = D("50")
        regra.save()
        t1 = ContaPagar.objects.create(fornecedor=self.forn, descricao="A", valor=D("1000"), vencimento=self.hoje)
        v1 = valor_retencao(t1, regra, D("1000"))
        self.assertEqual(v1, D("15.00") if regra.aliquota == D("1.5") else min((D("1000") * regra.aliquota / 100).quantize(D("0.01")), D("50")))
        Retencao.objects.create(conta_pagar=t1, regra=regra, base=D("1000"), aliquota=regra.aliquota, valor=D("45"))
        t2 = ContaPagar.objects.create(fornecedor=self.forn, descricao="B", valor=D("1000"), vencimento=self.hoje)
        self.assertEqual(valor_retencao(t2, regra, D("1000")), min((D("1000") * regra.aliquota / 100).quantize(D("0.01")), D("5.00")))
        # o próprio título não conta contra si mesmo
        self.assertEqual(valor_retencao(t1, regra, D("1000")), v1)

    def test_sem_teto_nao_limita(self):
        regra = RegraRetencao.objects.get(tributo="IRRF")
        t = ContaPagar.objects.create(fornecedor=self.forn, descricao="A", valor=D("1000"), vencimento=self.hoje)
        self.assertEqual(valor_retencao(t, regra, D("1000")), (D("1000") * regra.aliquota / 100).quantize(D("0.01")))


class BorderoTest(Base):
    def test_recolhe_varias_guias(self):
        a, b = self.guia("100"), self.guia("200", dias=10, tributo="PIS", codigo="5952")
        bd = BorderoImposto.recolher([a.pk, b.pk], self.banco, self.hoje, D("1"), self.fin)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.status, b.status), ("paga", "paga"))
        self.assertTrue(bd.numero.startswith(f"BIM-{self.hoje.year}-"))
        self.assertEqual(bd.total, a.total + b.total)
        self.assertEqual(bd.itens.count(), 2)
        self.assertGreater(b.multa, 0)

    def test_estorno_desfaz_tudo(self):
        a, b = self.guia("100"), self.guia("200", dias=5)
        bd = BorderoImposto.recolher([a.pk, b.pk], self.banco, self.hoje, 0, self.fin)
        bd.estornar()
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.status, b.status, bd.status), ("aberta", "aberta", "estornado"))
        self.assertFalse(Movimentacao.objects.exists())
        with self.assertRaises(ValidationError):
            bd.estornar()

    def test_guia_ja_paga_cancela_tudo(self):
        a, b = self.guia("100"), self.guia("200")
        b.recolher(self.banco, self.hoje)
        with self.assertRaises(ValidationError):
            BorderoImposto.recolher([a.pk, b.pk], self.banco, self.hoje, 0, self.fin)
        a.refresh_from_db()
        self.assertEqual(a.status, "aberta")
        self.assertEqual(BorderoImposto.objects.count(), 0)

    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            BorderoImposto.recolher([], self.banco, self.hoje)
        a = self.guia()
        self.banco.ativa = False
        self.banco.save()
        with self.assertRaises(ValidationError):
            BorderoImposto.recolher([a.pk], self.banco, self.hoje)

    def test_telas(self):
        a = self.guia("100", dias=3)
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse("financeiro:bordero_imposto")).status_code, 200)
        r = self.client.post(reverse("financeiro:bordero_imposto"), {
            "guias": [a.pk], "conta_bancaria": self.banco.pk, "data": self.hoje.isoformat(), "selic_pct": "1,5"})
        self.assertEqual(r.status_code, 302)
        bd = BorderoImposto.objects.get()
        self.assertEqual(self.client.get(bd.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:guia_darf", args=[a.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("financeiro:bordero_imposto_estornar", args=[bd.pk])).status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.status, "aberta")

    def test_sem_permissao(self):
        u = User.objects.create_user("x", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("financeiro:bordero_imposto")).status_code, 403)

    def test_recolher_guia_unica_com_encargos_pela_tela(self):
        g = self.guia("100", dias=3)
        self.client.force_login(self.fin)
        url = reverse("financeiro:guia_recolher", args=[g.pk])
        r = self.client.post(url, {"conta_bancaria": self.banco.pk, "data": self.hoje.isoformat(),
                                    "multa": "0,99", "juros": "1,00"})
        self.assertEqual(r.status_code, 302)
        g.refresh_from_db()
        self.assertEqual((g.multa, g.juros), (D("0.99"), D("1.00")))
