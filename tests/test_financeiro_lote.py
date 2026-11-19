import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cliente
from apps.financeiro.lote import LiquidacaoLote
from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.outro = User.objects.create_user("outro", password="x")
        cls.banco = ContaBancaria.objects.create(nome="Banco")
        cls.hoje = timezone.localdate()
        cls.cli = Cliente.objects.create(nome="Cliente SA", documento="11222333000181")
        cls.forn = cls.cli.adicionar_papel("fornecedor")

    def receber(self, valor="100", **kw):
        return ContaReceber.objects.create(cliente=self.cli, descricao="Venda", valor=D(valor), vencimento=self.hoje, **kw)

    def pagar(self, valor="100", **kw):
        return ContaPagar.objects.create(fornecedor=self.forn, descricao="Compra", valor=D(valor), vencimento=self.hoje, **kw)

    def lote(self, tipo, titulos, total, **kw):
        return LiquidacaoLote.liquidar(tipo, [t.pk for t in titulos], self.banco, self.hoje, D(total), usuario=self.fin, **kw)


class LoteTest(Base):
    def test_liquida_todos_pelo_saldo(self):
        a, b = self.receber("100"), self.receber("250.50")
        l = self.lote("receber", [a, b], "350.50", forma_pagamento="pix")
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.status, b.status), ("pago", "pago"))
        self.assertEqual((l.quantidade, l.total, l.liquido), (2, D("350.50"), D("350.50")))
        self.assertTrue(l.numero.startswith(f"LQL-{self.hoje.year}-"))
        self.assertEqual(l.itens.count(), 2)
        self.assertEqual(self.banco.saldo_atual, D("350.50"))

    def test_paga_titulo_parcialmente_pago_pelo_saldo(self):
        a = self.pagar("100")
        a.baixar(D("40"), self.hoje, self.banco)
        l = self.lote("pagar", [a], "60")
        a.refresh_from_db()
        self.assertEqual((a.status, l.total), ("pago", D("60.00")))

    def test_total_conferido_precisa_bater(self):
        a = self.receber("100")
        with self.assertRaises(ValidationError):
            self.lote("receber", [a], "99")
        a.refresh_from_db()
        self.assertEqual(a.status, "aberto")

    def test_tudo_ou_nada(self):
        a, b = self.receber("100"), self.receber("50")
        b.status = "cancelado"
        b.save()
        with self.assertRaises(ValidationError):
            self.lote("receber", [a, b], "150")
        a.refresh_from_db()
        self.assertEqual(a.status, "aberto")
        self.assertEqual(LiquidacaoLote.objects.count(), 0)
        self.assertEqual(self.banco.movimentacoes.count(), 0)

    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            LiquidacaoLote.liquidar("receber", [], self.banco, self.hoje, D("0"))
        with self.assertRaises(ValidationError):
            LiquidacaoLote.liquidar("outro", [1], self.banco, self.hoje, D("1"))
        with self.assertRaises(ValidationError):
            LiquidacaoLote.liquidar("receber", [999999], self.banco, self.hoje, D("1"))
        inativa = ContaBancaria.objects.create(nome="Velha", ativa=False)
        a = self.receber("10")
        with self.assertRaises(ValidationError):
            LiquidacaoLote.liquidar("receber", [a.pk], inativa, self.hoje, D("10"))

    def test_duplicados_na_selecao_contam_uma_vez(self):
        a = self.receber("100")
        l = LiquidacaoLote.liquidar("receber", [a.pk, a.pk], self.banco, self.hoje, D("100"))
        self.assertEqual(l.quantidade, 1)

    def test_estorno_do_lote(self):
        a, b = self.receber("100"), self.receber("50")
        l = self.lote("receber", [a, b], "150")
        l.estornar()
        a.refresh_from_db(); b.refresh_from_db(); l.refresh_from_db()
        self.assertEqual((a.status, b.status, l.status), ("aberto", "aberto", "estornada"))
        self.assertEqual(self.banco.movimentacoes.count(), 0)
        with self.assertRaises(ValidationError):
            l.estornar()

    def test_estorno_recusado_se_titulo_tem_outro_pagamento(self):
        a = self.receber("100")
        l = self.lote("receber", [a], "100")
        a.refresh_from_db()
        a.estornar()
        a.baixar(D("30"), self.hoje, self.banco)
        with self.assertRaises(ValidationError):
            l.estornar()
        l.refresh_from_db()
        self.assertEqual(l.status, "efetivada")


class TelasTest(Base):
    def test_fluxo(self):
        a, b = self.receber("100"), self.receber("40")
        self.client.force_login(self.fin)
        r = self.client.get(reverse("financeiro:liquidar_lote_receber"))
        self.assertContains(r, "Venda")
        r = self.client.post(reverse("financeiro:liquidar_lote_receber"), {
            "titulos": [a.pk, b.pk], "conta_bancaria": self.banco.pk, "data": self.hoje.isoformat(),
            "forma_pagamento": "pix", "total_conferido": "140,00"})
        self.assertEqual(r.status_code, 302)
        l = LiquidacaoLote.objects.get()
        self.assertEqual(l.quantidade, 2)
        self.client.post(reverse("financeiro:lote_estornar", args=[l.pk]))
        l.refresh_from_db()
        self.assertEqual(l.status, "estornada")

    def test_total_errado_nao_liquida(self):
        a = self.receber("100")
        self.client.force_login(self.fin)
        r = self.client.post(reverse("financeiro:liquidar_lote_receber"), {
            "titulos": [a.pk], "conta_bancaria": self.banco.pk, "data": self.hoje.isoformat(), "total_conferido": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(LiquidacaoLote.objects.count(), 0)

    def test_permissoes_e_filtro(self):
        self.client.force_login(self.outro)
        self.assertEqual(self.client.get(reverse("financeiro:liquidar_lote_pagar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("financeiro:lote_estornar", args=[1])).status_code, 403)
        self.client.force_login(self.fin)
        self.receber("10", documento="NF-77")
        ontem = (self.hoje - datetime.timedelta(days=1)).isoformat()
        self.assertContains(self.client.get(reverse("financeiro:liquidar_lote_receber") + "?q=NF-77"), "NF-77")
        self.assertNotContains(self.client.get(reverse("financeiro:liquidar_lote_receber") + f"?ate={ontem}"), "NF-77")
        self.assertEqual(self.client.get(reverse("financeiro:liquidacaolote_list")).status_code, 200)
