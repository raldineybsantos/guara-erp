import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Cliente
from apps.estoque import models as est
from apps.estoque.models import Armazem, Movimento, Saldo
from apps.financeiro.models import ContaReceber
from apps.fiscal.models import Produto
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


class VendasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.vendedor = User.objects.create_user("vendedor", password="x")
        cls.vendedor.groups.add(Group.objects.get(name="Vendas"))
        cls.fin = User.objects.create_user("fin", password="x", first_name="Fernanda")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.cli = Cliente.objects.create(nome="Mercado Bom", limite_credito=1000)
        cls.cadeira = Produto.objects.create(codigo="CAD", descricao="Cadeira", preco=200, aliquota_icms=18)
        cls.montagem = Produto.objects.create(codigo="MON", descricao="Montagem", tipo="servico", preco=50)
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)

    def setUp(self):
        est.entrada(self.cadeira, 10, "120.00", self.a1)

    def _pedido(self, qtd=3, desconto=0, cond="30"):
        p = PedidoVenda.objects.create(cliente=self.cli, condicao_pagamento=cond)
        ItemPedidoVenda.objects.create(pedido=p, produto=self.cadeira, quantidade=qtd, desconto_pct=desconto)
        ItemPedidoVenda.objects.create(pedido=p, produto=self.montagem, quantidade=1)
        p.recalcular()
        return p

    def test_preco_do_cadastro_desconto_e_total(self):
        p = self._pedido(qtd=2, desconto=10)
        item = p.itens.get(produto=self.cadeira)
        self.assertEqual((item.valor_unitario, item.valor_unitario_liquido), (Decimal("200"), Decimal("180.0000")))
        self.assertEqual(p.valor_total, Decimal("410.00"))

    def test_faturar_gera_nota_titulos_e_baixa_estoque(self):
        p = self._pedido(cond="30/60")
        self.assertTrue(p.confirmar())
        nota = p.faturar()
        p.refresh_from_db()
        self.assertEqual((p.status, p.nota_fiscal), ("faturado", nota))
        self.assertEqual((nota.tipo, nota.status, nota.valor_total), ("saida", "emitida", Decimal("650.00")))
        self.assertEqual(nota.valor_icms, Decimal("108.00"))
        contas = ContaReceber.objects.filter(cliente=self.cli).order_by("parcela")
        self.assertEqual([c.valor for c in contas], [Decimal("325.00"), Decimal("325.00")])
        self.assertEqual(contas[1].vencimento - nota.data_emissao, datetime.timedelta(days=60))
        self.assertEqual(Saldo.objects.get(produto=self.cadeira).quantidade, Decimal("7"))
        mov = Movimento.objects.get(origem="venda")
        self.assertEqual((mov.custo_unitario, mov.referencia), (Decimal("120.0000"), p))
        with self.assertRaises(ValidationError):
            p.faturar()

    def test_estoque_insuficiente(self):
        p = self._pedido(qtd=11)
        with self.assertRaisesMessage(ValidationError, "Estoque insuficiente"):
            p.confirmar()

    def test_credito_bloqueia_e_financeiro_libera(self):
        ContaReceber.objects.create(cliente=self.cli, descricao="antiga", valor=500, vencimento="2026-12-01")
        p = self._pedido()  # 650 + 500 em aberto > 1000
        self.assertFalse(p.confirmar())
        self.assertEqual(p.status, "bloqueado")
        self.assertIn("Limite de crédito", p.motivo_status)
        with self.assertRaises(ValidationError):
            p.faturar()
        # O vendedor não pode liberar o próprio crédito; o Financeiro pode.
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("vendas:liberar_credito", args=[p.pk])).status_code, 403)
        self.client.force_login(self.fin)
        self.client.post(reverse("vendas:liberar_credito", args=[p.pk]), {"motivo": "Cliente antigo, pagou em dia"})
        p.refresh_from_db()
        self.assertEqual(p.status, "aprovado")
        self.assertIn("Fernanda", p.motivo_status)

    def test_desconto_acima_do_maximo(self):
        p = PedidoVenda.objects.create(cliente=self.cli)
        item = ItemPedidoVenda(pedido=p, produto=self.cadeira, quantidade=1, desconto_pct=15)
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_telas(self):
        self.client.force_login(self.vendedor)
        r = self.client.post(reverse("vendas:pedidovenda_create"), {
            "cliente": self.cli.pk, "data_pedido": "2026-11-13", "condicao_pagamento": "28",
            "natureza_operacao": "Venda", "valor_frete": "0",
            "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0",
            "itens-0-produto": self.cadeira.pk, "itens-0-quantidade": "2", "itens-0-valor_unitario": "0",
            "itens-0-desconto_pct": "0"})
        self.assertEqual(r.status_code, 302, r.content[:3000])
        p = PedidoVenda.objects.get()
        self.assertEqual((p.vendedor, p.valor_total), (self.vendedor, Decimal("400.00")))
        self.client.post(reverse("vendas:confirmar", args=[p.pk]))
        self.client.post(reverse("vendas:faturar", args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual(p.status, "faturado")
        r = self.client.post(reverse("vendas:pedidovenda_update", args=[p.pk]), {
            "cliente": self.cli.pk, "data_pedido": "2026-11-13", "condicao_pagamento": "28",
            "natureza_operacao": "Venda", "valor_frete": "0", "itens-TOTAL_FORMS": "0", "itens-INITIAL_FORMS": "0"})
        self.assertContains(r, "não pode ser alterado")
        self.assertEqual(self.client.get(p.get_absolute_url()).status_code, 200)
