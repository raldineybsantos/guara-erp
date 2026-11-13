from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.compras.models import ItemPedido, PedidoCompra
from apps.core.models import Fornecedor
from apps.estoque import models as est
from apps.estoque.models import Armazem, Inventario, Movimento, Saldo
from apps.fiscal.models import Produto


class EstoqueTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.almox = User.objects.create_user("almox", password="x")
        cls.almox.groups.add(Group.objects.get(name="Estoque"))
        cls.p = Produto.objects.create(codigo="P1", descricao="Parafuso", estoque_minimo=50)
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almoxarifado", padrao=True)
        cls.a2 = Armazem.objects.create(codigo="02", nome="Expedição")

    def test_custo_medio_ponderado_e_kardex(self):
        est.entrada(self.p, 10, "2.00", self.a1)
        est.entrada(self.p, 30, "3.00", self.a1)
        s = Saldo.objects.get(produto=self.p, armazem=self.a1)
        self.assertEqual((s.quantidade, s.custo_medio), (Decimal("40"), Decimal("2.75")))
        m = est.saida(self.p, 15, self.a1, "consumo", "REQ-1")
        self.assertEqual(m.custo_unitario, Decimal("2.75"))
        s.refresh_from_db()
        self.assertEqual(s.quantidade, Decimal("25"))
        self.assertEqual(list(Movimento.objects.order_by("id").values_list("saldo_apos", flat=True)),
                         [Decimal("10"), Decimal("40"), Decimal("25")])

    def test_saldo_negativo_bloqueado_e_servico_sem_estoque(self):
        est.entrada(self.p, 5, 1, self.a1)
        with self.assertRaisesMessage(ValidationError, "Saldo insuficiente"):
            est.saida(self.p, 6, self.a1)
        serv = Produto.objects.create(codigo="S1", descricao="Consultoria", tipo="servico")
        with self.assertRaises(ValidationError):
            est.entrada(serv, 1, 100)

    def test_transferencia_mantem_custo(self):
        est.entrada(self.p, 10, "4.00", self.a1)
        est.transferir(self.p, 4, self.a1, self.a2)
        self.assertEqual(Saldo.objects.get(produto=self.p, armazem=self.a2).custo_medio, Decimal("4.00"))
        self.assertEqual(est.saldo_disponivel(self.p), Decimal("10"))

    def test_inventario_ajusta_para_o_contado(self):
        est.entrada(self.p, 100, 1, self.a1)
        inv = Inventario.objects.create(armazem=self.a1)
        inv.carregar_itens()
        item = inv.itens.get()
        self.assertEqual(item.quantidade_sistema, Decimal("100"))
        with self.assertRaisesMessage(ValidationError, "Falta a contagem"):
            inv.encerrar()
        item.quantidade_contada = 97
        item.save()
        self.assertEqual(inv.encerrar(), 1)
        self.assertEqual(Saldo.objects.get(produto=self.p, armazem=self.a1).quantidade, Decimal("97"))
        self.assertEqual(Movimento.objects.filter(origem="inventario").get().quantidade, Decimal("3"))

    def test_recebimento_de_compra_da_entrada_pelo_preco_do_pedido(self):
        forn = Fornecedor.objects.create(nome="F")
        pc = PedidoCompra.objects.create(fornecedor=forn, status="aprovado")
        item = ItemPedido.objects.create(pedido=pc, produto=self.p, quantidade=20, valor_unitario="1.50")
        rec = pc.receber({str(item.pk): 20})
        s = Saldo.objects.get(produto=self.p, armazem=self.a1)
        self.assertEqual((s.quantidade, s.custo_medio), (Decimal("20"), Decimal("1.50")))
        mov = Movimento.objects.get(origem="compra")
        self.assertEqual((mov.documento, mov.referencia), (pc.numero, rec))

    def test_telas(self):
        self.client.force_login(self.almox)
        r = self.client.post(reverse("estoque:movimentar"), {
            "operacao": "entrada", "produto": self.p.pk, "armazem": self.a1.pk, "quantidade": "10",
            "custo_unitario": "2", "motivo": "Saldo inicial"})
        self.assertRedirects(r, reverse("estoque:movimento_list"))
        r = self.client.post(reverse("estoque:movimentar"), {
            "operacao": "saida", "produto": self.p.pk, "armazem": self.a1.pk, "quantidade": "11", "motivo": "x"})
        self.assertContains(r, "Saldo insuficiente")
        self.assertContains(self.client.get(reverse("estoque:reposicao")), "Parafuso")
        r = self.client.post(reverse("estoque:reposicao"))
        self.assertEqual(r.status_code, 302)
        from apps.compras.models import SolicitacaoCompra

        self.assertEqual(SolicitacaoCompra.objects.get().itens.get().quantidade, Decimal("90"))
        r = self.client.post(reverse("estoque:inventario_create"), {
            "armazem": self.a1.pk, "data": "2026-11-13", "itens-TOTAL_FORMS": "0", "itens-INITIAL_FORMS": "0"})
        inv = Inventario.objects.get()
        self.assertEqual(inv.itens.count(), 1)
        self.client.post(reverse("estoque:encerrar_inventario", args=[inv.pk]))  # sem contagem: recusa
        inv.refresh_from_db()
        self.assertEqual(inv.status, "aberto")
        for nome in ("saldo", "movimento", "inventario", "armazem"):
            self.assertEqual(self.client.get(reverse(f"estoque:{nome}_list")).status_code, 200, nome)
        self.assertEqual(self.client.get(reverse("estoque:saldo_detail", args=[Saldo.objects.first().pk])
                                         ).status_code, 200)
