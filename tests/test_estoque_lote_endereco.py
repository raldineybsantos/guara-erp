import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.compras.models import ItemPedido, PedidoCompra
from apps.core.models import Fornecedor
from apps.estoque import models as est
from apps.estoque.models import Armazem, Endereco, Lote, Saldo, SaldoLote
from apps.fiscal.models import Produto


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.user = User.objects.create_user("almox", password="x")
        cls.user.groups.add(Group.objects.get(name="Estoque"))
        cls.hoje = timezone.localdate()
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.a2 = Armazem.objects.create(codigo="02", nome="Outro")
        cls.p = Produto.objects.create(codigo="LEITE", descricao="Leite", controla_lote=True)
        cls.sem = Produto.objects.create(codigo="CAIXA", descricao="Caixa")

    def dia(self, n):
        return self.hoje + datetime.timedelta(days=n)

    def soma_linhas(self, produto=None, armazem=None):
        qs = SaldoLote.objects.filter(produto=produto or self.p, armazem=armazem or self.a1)
        return sum((l.quantidade for l in qs), D("0"))


class LoteTest(Base):
    def test_entrada_cria_lote_e_linha_e_soma_bate_com_saldo(self):
        est.entrada(self.p, 10, 2, self.a1, lote="L1", validade=self.dia(60))
        est.entrada(self.p, 5, 2, self.a1, lote="L2", validade=self.dia(30))
        est.entrada(self.p, 3, 2, self.a1, lote="L1")
        self.assertEqual(Lote.objects.count(), 2)
        self.assertEqual(Lote.objects.get(codigo="L1").quantidade, D("13"))
        self.assertEqual(self.soma_linhas(), Saldo.objects.get(produto=self.p, armazem=self.a1).quantidade)

    def test_validade_diferente_para_lote_existente_recusada(self):
        est.entrada(self.p, 1, 1, self.a1, lote="L1", validade=self.dia(60))
        with self.assertRaisesMessage(ValidationError, "já existe com validade"):
            est.entrada(self.p, 1, 1, self.a1, lote="L1", validade=self.dia(90))

    def test_sem_lote_informado_cria_lote_automatico(self):
        est.entrada(self.p, 4, 1, self.a1, documento="PC-7", origem="compra")
        self.assertTrue(Lote.objects.get().codigo.startswith("PC-7-"))

    def test_fefo_consome_o_que_vence_antes_e_divide_entre_lotes(self):
        est.entrada(self.p, 10, 2, self.a1, lote="LONGO", validade=self.dia(90))
        est.entrada(self.p, 6, 2, self.a1, lote="CURTO", validade=self.dia(10))
        est.entrada(self.p, 5, 2, self.a1, lote="SEMVAL")
        mov = est.saida(self.p, 8, self.a1, "venda")
        partes = {(x.lote.codigo, x.quantidade) for x in mov.partes.all()}
        self.assertEqual(partes, {("CURTO", D("6")), ("LONGO", D("2"))})
        self.assertEqual(Lote.objects.get(codigo="CURTO").quantidade, D("0"))
        self.assertEqual(self.soma_linhas(), D("13"))

    def test_saida_de_lote_especifico(self):
        est.entrada(self.p, 10, 2, self.a1, lote="A", validade=self.dia(10))
        est.entrada(self.p, 10, 2, self.a1, lote="B", validade=self.dia(50))
        est.saida(self.p, 4, self.a1, "venda", lote="B")
        self.assertEqual(Lote.objects.get(codigo="B").quantidade, D("6"))
        with self.assertRaisesMessage(ValidationError, "Saldo utilizável insuficiente"):
            est.saida(self.p, 7, self.a1, "venda", lote="B")

    def test_lote_vencido_e_bloqueado_nao_saem_em_venda(self):
        est.entrada(self.p, 5, 2, self.a1, lote="VELHO", validade=self.dia(-3))
        est.entrada(self.p, 5, 2, self.a1, lote="OK", validade=self.dia(20))
        mov = est.saida(self.p, 5, self.a1, "venda")
        self.assertEqual([x.lote.codigo for x in mov.partes.all()], ["OK"])
        with self.assertRaisesMessage(ValidationError, "lotes vencidos ou bloqueados"):
            est.saida(self.p, 1, self.a1, "venda")
        with self.assertRaisesMessage(ValidationError, "venceu em"):
            est.saida(self.p, 1, self.a1, "venda", lote="VELHO")
        est.entrada(self.p, 5, 2, self.a1, lote="BLOQ", validade=self.dia(20))
        b = Lote.objects.get(codigo="BLOQ")
        b.bloqueado, b.motivo_bloqueio = True, "Análise"
        b.save()
        with self.assertRaisesMessage(ValidationError, "bloqueado"):
            est.saida(self.p, 1, self.a1, "venda", lote="BLOQ")
        self.assertEqual(est.saldo_disponivel(self.p), D("0"))     # vencido e bloqueado não contam

    def test_descarte_de_lote_vencido_por_transferencia(self):
        est.entrada(self.p, 5, 2, self.a1, lote="VELHO", validade=self.dia(-3))
        est.transferir(self.p, 5, self.a1, self.a2, "DESC")
        linha = SaldoLote.objects.get(armazem=self.a2)
        self.assertEqual((linha.lote.codigo, linha.quantidade), ("VELHO", D("5")))
        self.assertEqual(self.soma_linhas(), D("0"))

    def test_transferencia_preserva_lotes_em_um_unico_movimento(self):
        est.entrada(self.p, 3, 2, self.a1, lote="A", validade=self.dia(10))
        est.entrada(self.p, 3, 2, self.a1, lote="B", validade=self.dia(20))
        est.transferir(self.p, 5, self.a1, self.a2, "T1")
        destino = {(l.lote.codigo, l.quantidade) for l in SaldoLote.objects.filter(armazem=self.a2)}
        self.assertEqual(destino, {("A", D("3")), ("B", D("2"))})
        self.assertEqual(self.soma_linhas(armazem=self.a2), Saldo.objects.get(armazem=self.a2).quantidade)

    def test_produto_sem_controle_nao_gera_linhas(self):
        est.entrada(self.sem, 5, 1, self.a1)
        est.saida(self.sem, 2, self.a1)
        self.assertFalse(SaldoLote.objects.exists())

    def test_ajuste_de_inventario_a_menos_usa_fefo_e_a_mais_cria_lote(self):
        est.entrada(self.p, 10, 2, self.a1, lote="A", validade=self.dia(10))
        est.ajustar(self.p, self.a1, 7, "inventario", "INV-1")
        self.assertEqual(Lote.objects.get(codigo="A").quantidade, D("7"))
        est.ajustar(self.p, self.a1, 9, "inventario", "INV-1")
        self.assertEqual(self.soma_linhas(), D("9"))

    def test_data_de_validade_menor_que_fabricacao(self):
        l = Lote(produto=self.p, codigo="X", fabricacao=self.dia(5), validade=self.dia(1))
        with self.assertRaises(ValidationError):
            l.full_clean()


class EnderecoTest(Base):
    def test_endereco_de_outro_armazem_recusado(self):
        e2 = Endereco.objects.create(armazem=self.a2, codigo="A-01")
        with self.assertRaisesMessage(ValidationError, "não pertence"):
            est.entrada(self.p, 1, 1, self.a1, lote="L", endereco=e2)

    def test_produto_so_com_endereco_e_enderecar(self):
        prod = Produto.objects.create(codigo="PAR", descricao="Parafuso", controla_endereco=True)
        e1 = Endereco.objects.create(armazem=self.a1, codigo="A-01")
        e2 = Endereco.objects.create(armazem=self.a1, codigo="B-02")
        est.entrada(prod, 10, 1, self.a1, endereco=e1)
        est.enderecar(prod, 4, self.a1, None, e1, e2)
        q = {l.endereco.codigo: l.quantidade for l in SaldoLote.objects.filter(produto=prod, quantidade__gt=0)}
        self.assertEqual(q, {"A-01": D("6"), "B-02": D("4")})
        self.assertEqual(Saldo.objects.get(produto=prod).quantidade, D("10"))       # saldo não muda
        with self.assertRaises(ValidationError):
            est.enderecar(prod, 7, self.a1, None, e2, e1)                            # só há 4 lá
        with self.assertRaises(ValidationError):
            est.saida(prod, 8, self.a1, "consumo", endereco=e1)     # só há 6 em A-01

    def test_saida_por_endereco(self):
        prod = Produto.objects.create(codigo="PAR", descricao="Parafuso", controla_endereco=True)
        e1 = Endereco.objects.create(armazem=self.a1, codigo="A-01")
        e2 = Endereco.objects.create(armazem=self.a1, codigo="B-02")
        est.entrada(prod, 5, 1, self.a1, endereco=e1)
        est.entrada(prod, 5, 1, self.a1, endereco=e2)
        est.saida(prod, 4, self.a1, "consumo", endereco=e2)
        self.assertEqual(SaldoLote.objects.get(produto=prod, endereco=e2).quantidade, D("1"))
        self.assertEqual(SaldoLote.objects.get(produto=prod, endereco=e1).quantidade, D("5"))


class RecebimentoELatelasTest(Base):
    def test_recebimento_de_compra_com_lote_e_validade(self):
        forn = Fornecedor.objects.create(nome="Laticínios")
        pedido = PedidoCompra.objects.create(fornecedor=forn, status="aprovado")
        item = ItemPedido.objects.create(pedido=pedido, produto=self.p, descricao="Leite", quantidade=20,
                                         valor_unitario=D("4"))
        pedido.receber({str(item.pk): D("20")}, lotes={str(item.pk): ("LT-55", self.dia(45))})
        lote = Lote.objects.get()
        self.assertEqual((lote.codigo, lote.validade, lote.quantidade), ("LT-55", self.dia(45), D("20")))

    def test_telas(self):
        self.client.force_login(self.user)
        est.entrada(self.p, 5, 2, self.a1, lote="VELHO", validade=self.dia(-2))
        est.entrada(self.p, 5, 2, self.a1, lote="NOVO", validade=self.dia(15))
        est.entrada(self.p, 5, 2, self.a1, lote="LONGE", validade=self.dia(200))
        r = self.client.get(reverse("estoque:lotes_a_vencer"), {"dias": 30})
        self.assertContains(r, "VELHO")
        self.assertContains(r, "NOVO")
        self.assertNotContains(r, "LONGE")
        for nome in ("lote_list", "saldolote_list", "endereco_list"):
            self.assertEqual(self.client.get(reverse(f"estoque:{nome}")).status_code, 200, nome)
        lote = Lote.objects.get(codigo="NOVO")
        self.assertEqual(self.client.get(lote.get_absolute_url()).status_code, 200)
        r = self.client.post(reverse("estoque:movimentar"), {
            "operacao": "saida", "produto": self.p.pk, "armazem": self.a1.pk, "quantidade": "3", "lote": "NOVO",
            "motivo": "Consumo"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Lote.objects.get(codigo="NOVO").quantidade, D("2"))

    def test_sem_permissao(self):
        self.client.force_login(User.objects.create_user("x", password="x"))
        self.assertEqual(self.client.get(reverse("estoque:lotes_a_vencer")).status_code, 403)
