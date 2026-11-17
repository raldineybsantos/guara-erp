import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cliente
from apps.estoque import models as est
from apps.estoque.models import Armazem
from apps.financeiro.models import ContaBancaria, ContaPagar
from apps.fiscal.models import Produto
from apps.vendas.models import (Comissao, ItemPedidoVenda, ItemTabelaPreco, PedidoVenda, RegraComissao,
                                TabelaPreco)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.vendedor = User.objects.create_user("vendedor", password="x")
        cls.vendedor.groups.add(Group.objects.get(name="Vendas"))
        cls.outro = User.objects.create_user("outro", password="x")
        cls.outro.groups.add(Group.objects.get(name="Vendas"))
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.cli = Cliente.objects.create(nome="Mercado Bom")
        cls.cli2 = Cliente.objects.create(nome="Outro Mercado")
        cls.cadeira = Produto.objects.create(codigo="CAD", descricao="Cadeira", preco=200, aliquota_icms=18)
        cls.mesa = Produto.objects.create(codigo="MES", descricao="Mesa", preco=500)
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.hoje = timezone.localdate()
        cls.banco = ContaBancaria.objects.create(nome="Banco")

    def setUp(self):
        est.entrada(self.cadeira, 100, "120.00", self.a1)
        est.entrada(self.mesa, 100, "300.00", self.a1)

    def tabela(self, nome="Atacado", padrao=True, preco="150", minimo=1, **kw):
        t = TabelaPreco.objects.create(nome=nome, padrao=padrao, **kw)
        ItemTabelaPreco.objects.create(tabela=t, produto=self.cadeira, quantidade_minima=minimo, preco=D(preco))
        return t

    def pedido(self, cliente=None, qtd=2, vendedor=None, produto=None):
        p = PedidoVenda.objects.create(cliente=cliente or self.cli, vendedor=vendedor or self.vendedor)
        ItemPedidoVenda.objects.create(pedido=p, produto=produto or self.cadeira, quantidade=qtd)
        p.recalcular()
        p.refresh_from_db()
        return p


class TabelaPrecoTest(Base):
    def test_sem_tabela_usa_o_preco_do_cadastro(self):
        p = self.pedido()
        self.assertIsNone(p.tabela_preco)
        self.assertEqual(p.itens.get().valor_unitario, D("200"))

    def test_tabela_padrao_vigente_e_aplicada(self):
        t = self.tabela()
        p = self.pedido()
        self.assertEqual(p.tabela_preco, t)
        self.assertEqual(p.itens.get().valor_unitario, D("150"))
        self.assertEqual(p.valor_itens, D("300.00"))

    def test_produto_fora_da_tabela_cai_no_cadastro(self):
        self.tabela()
        p = self.pedido(produto=self.mesa)
        self.assertEqual(p.itens.get().valor_unitario, D("500"))

    def test_faixa_por_quantidade(self):
        t = self.tabela(preco="150", minimo=1)
        ItemTabelaPreco.objects.create(tabela=t, produto=self.cadeira, quantidade_minima=10, preco=D("120"))
        self.assertEqual(t.preco_para(self.cadeira, 5), D("150"))
        self.assertEqual(t.preco_para(self.cadeira, 10), D("120"))
        self.assertEqual(t.preco_para(self.cadeira, 50), D("120"))
        t2 = self.tabela(nome="Só acima de 5", preco="100", minimo=5, padrao=False)
        self.assertIsNone(t2.preco_para(self.cadeira, 2))

    def test_vigencia(self):
        passada = self.tabela(nome="Antiga", fim=self.hoje - datetime.timedelta(days=1),
                              inicio=self.hoje - datetime.timedelta(days=30))
        futura = self.tabela(nome="Futura", inicio=self.hoje + datetime.timedelta(days=5))
        self.assertIsNone(TabelaPreco.para_cliente(self.cli, self.hoje))
        self.assertEqual(TabelaPreco.para_cliente(self.cli, self.hoje + datetime.timedelta(days=5)), futura)
        self.assertIsNone(passada.preco_para(self.cadeira, 1, self.hoje))
        self.assertEqual(passada.preco_para(self.cadeira, 1, self.hoje - datetime.timedelta(days=2)), D("150"))

    def test_tabela_inativa_nao_vale(self):
        self.tabela(ativa=False)
        self.assertIsNone(TabelaPreco.para_cliente(self.cli, self.hoje))

    def test_tabela_exclusiva_do_cliente_vence_a_padrao(self):
        padrao = self.tabela(nome="Padrão", preco="190")
        vip = self.tabela(nome="VIP", padrao=False, preco="130")
        vip.clientes.add(self.cli)
        self.assertEqual(TabelaPreco.para_cliente(self.cli, self.hoje), vip)
        self.assertEqual(TabelaPreco.para_cliente(self.cli2, self.hoje), padrao)
        self.assertEqual(self.pedido(self.cli).itens.get().valor_unitario, D("130"))
        self.assertEqual(self.pedido(self.cli2).itens.get().valor_unitario, D("190"))

    def test_tabela_exclusiva_de_outros_clientes_nao_serve_de_padrao(self):
        t = self.tabela(nome="Só VIP", padrao=True)
        t.clientes.add(self.cli)
        self.assertIsNone(TabelaPreco.para_cliente(self.cli2, self.hoje))

    def test_preco_digitado_prevalece(self):
        self.tabela()
        p = PedidoVenda.objects.create(cliente=self.cli)
        ItemPedidoVenda.objects.create(pedido=p, produto=self.cadeira, quantidade=1, valor_unitario=D("99"))
        self.assertEqual(p.itens.get().valor_unitario, D("99"))

    def test_preco_do_pedido_nao_muda_quando_a_tabela_muda(self):
        t = self.tabela()
        p = self.pedido()
        item = ItemTabelaPreco.objects.get(tabela=t)
        item.preco = D("10")
        item.save()
        self.assertEqual(p.itens.get().valor_unitario, D("150"))

    def test_item_da_tabela_valida_preco_e_faixa(self):
        t = self.tabela()
        with self.assertRaises(ValidationError):
            ItemTabelaPreco(tabela=t, produto=self.mesa, quantidade_minima=1, preco=D("0")).full_clean()
        with self.assertRaises(ValidationError):
            TabelaPreco(nome="x", inicio=self.hoje, fim=self.hoje - datetime.timedelta(days=1)).full_clean()

    def test_pedido_faturado_usa_o_preco_da_tabela(self):
        self.tabela()
        p = self.pedido(qtd=4)
        self.assertTrue(p.confirmar())
        nota = p.faturar()
        self.assertEqual(nota.valor_total, D("600.00"))


class ComissaoTest(Base):
    def faturar(self, p):
        self.assertTrue(p.confirmar())
        p.faturar()
        return p

    def test_sem_regra_nao_gera_comissao(self):
        self.faturar(self.pedido())
        self.assertFalse(Comissao.objects.exists())

    def test_regra_geral_e_regra_do_vendedor(self):
        RegraComissao.objects.create(percentual=D("2"), momento="faturamento")
        RegraComissao.objects.create(vendedor=self.outro, percentual=D("5"), momento="faturamento")
        self.faturar(self.pedido(vendedor=self.vendedor, qtd=5))       # 1000 → 2%
        self.faturar(self.pedido(vendedor=self.outro, qtd=5))          # 1000 → 5%
        c1 = Comissao.objects.get(vendedor=self.vendedor)
        c2 = Comissao.objects.get(vendedor=self.outro)
        self.assertEqual((c1.base, c1.percentual, c1.valor), (D("1000.00"), D("2.00"), D("20.00")))
        self.assertEqual(c2.valor, D("50.00"))

    def test_regra_inativa_ou_zero_nao_gera(self):
        RegraComissao.objects.create(percentual=D("3"), ativa=False)
        self.faturar(self.pedido())
        self.assertFalse(Comissao.objects.exists())

    def test_base_nao_inclui_frete(self):
        RegraComissao.objects.create(percentual=D("10"), momento="faturamento")
        p = self.pedido(qtd=1)
        p.valor_frete = D("100")
        p.save()
        p.recalcular()
        self.faturar(p)
        self.assertEqual(Comissao.objects.get().base, D("200.00"))

    def test_devida_no_faturamento_vale_o_total(self):
        RegraComissao.objects.create(percentual=D("10"), momento="faturamento")
        self.faturar(self.pedido())
        c = Comissao.objects.get()
        self.assertEqual(c.valor_devido, c.valor)
        self.assertEqual(c.valor, D("40.00"))

    def test_devida_no_recebimento_acompanha_o_que_o_cliente_pagou(self):
        RegraComissao.objects.create(percentual=D("10"), momento="recebimento")
        p = self.pedido(qtd=5)          # 1000 em 2 parcelas
        p.condicao_pagamento = "30/60"
        p.save()
        self.faturar(p)
        c = Comissao.objects.get()
        self.assertEqual(c.valor, D("100.00"))
        self.assertEqual(c.valor_devido, D("0.00"))
        t1, t2 = list(p.titulos().order_by("parcela"))
        t1.baixar(t1.saldo, self.hoje, self.banco)
        c = Comissao.objects.get()
        self.assertEqual(c.valor_devido, D("50.00"))
        t2.baixar(D("100"), self.hoje, self.banco)
        self.assertEqual(Comissao.objects.get().valor_devido, D("60.00"))
        t2.refresh_from_db()
        t2.baixar(t2.saldo, self.hoje, self.banco)
        self.assertEqual(Comissao.objects.get().valor_devido, D("100.00"))

    def test_estorno_do_recebimento_reduz_a_comissao_devida(self):
        RegraComissao.objects.create(percentual=D("10"), momento="recebimento")
        p = self.pedido(qtd=5)
        self.faturar(p)
        t = p.titulos().get()
        t.baixar(t.saldo, self.hoje, self.banco)
        self.assertEqual(Comissao.objects.get().valor_devido, D("100.00"))
        t.refresh_from_db()
        t.estornar()
        self.assertEqual(Comissao.objects.get().valor_devido, D("0.00"))

    def test_lancar_conta_a_pagar_so_do_que_e_devido_e_sem_duplicar(self):
        RegraComissao.objects.create(percentual=D("10"), momento="recebimento")
        p = self.pedido(qtd=5)
        self.faturar(p)
        c = Comissao.objects.get()
        with self.assertRaises(ValidationError):
            c.lancar_conta_pagar(self.hoje)            # nada devido ainda
        t = p.titulos().get()
        t.baixar(D("500"), self.hoje, self.banco)
        titulo = c.lancar_conta_pagar(self.hoje + datetime.timedelta(days=10))
        self.assertEqual(titulo.valor, D("50.00"))
        with self.assertRaises(ValidationError):
            Comissao.objects.get().lancar_conta_pagar(self.hoje)   # já lançado
        t.refresh_from_db()
        t.baixar(t.saldo, self.hoje, self.banco)
        segunda = Comissao.objects.get().lancar_conta_pagar(self.hoje)
        self.assertEqual(segunda.valor, D("50.00"))
        self.assertEqual(Comissao.objects.get().valor_lancado, D("100.00"))
        self.assertEqual(ContaPagar.objects.filter(documento=p.numero).count(), 2)

    def test_regra_valida_percentual_e_unicidade(self):
        with self.assertRaises(ValidationError):
            RegraComissao(percentual=D("101")).full_clean()
        RegraComissao.objects.create(vendedor=self.vendedor, percentual=D("1"))
        with self.assertRaises(Exception):
            RegraComissao.objects.create(vendedor=self.vendedor, percentual=D("2"))

    def test_nao_gera_duas_vezes(self):
        RegraComissao.objects.create(percentual=D("10"), momento="faturamento")
        p = self.faturar(self.pedido())
        self.assertIsNone(Comissao.gerar(p))
        self.assertEqual(Comissao.objects.count(), 1)


class TelasTest(Base):
    def test_vendedor_so_ve_as_proprias_comissoes_e_nao_lanca(self):
        RegraComissao.objects.create(percentual=D("10"), momento="faturamento")
        for v in (self.vendedor, self.outro):
            p = self.pedido(vendedor=v)
            p.confirmar()
            p.faturar()
        minha = Comissao.objects.get(vendedor=self.vendedor)
        alheia = Comissao.objects.get(vendedor=self.outro)
        self.client.force_login(self.vendedor)
        r = self.client.get(reverse("vendas:comissao_list"))
        self.assertContains(r, minha.pedido.numero)
        self.assertNotContains(r, alheia.pedido.numero)
        self.assertEqual(self.client.get(reverse("vendas:comissao_detail", args=[alheia.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("vendas:lancar_comissao", args=[minha.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("vendas:regracomissao_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("vendas:regracomissao_list")).status_code, 403)

    def test_financeiro_ve_todas_e_lanca_pela_tela(self):
        RegraComissao.objects.create(percentual=D("10"), momento="faturamento")
        p = self.pedido()
        p.confirmar()
        p.faturar()
        c = Comissao.objects.get()
        self.client.force_login(self.fin)
        self.assertContains(self.client.get(reverse("vendas:comissao_list")), p.numero)
        self.assertEqual(self.client.get(reverse("vendas:comissao_detail", args=[c.pk])).status_code, 200)
        url = reverse("vendas:lancar_comissao", args=[c.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"vencimento": (self.hoje + datetime.timedelta(days=15)).isoformat()})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ContaPagar.objects.get(documento=p.numero).valor, D("40.00"))
        self.assertEqual(self.client.get(reverse("vendas:regracomissao_create")).status_code, 200)

    def test_comissao_somente_leitura(self):
        RegraComissao.objects.create(percentual=D("10"), momento="faturamento")
        p = self.pedido()
        p.confirmar()
        p.faturar()
        c = Comissao.objects.get()
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse("vendas:comissao_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("vendas:comissao_update", args=[c.pk])).status_code, 403)

    def test_vendedor_cria_tabela_de_preco_pela_tela(self):
        self.client.force_login(self.vendedor)
        self.assertEqual(self.client.get(reverse("vendas:tabelapreco_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("vendas:tabelapreco_list")).status_code, 200)

    def test_pedido_mostra_a_tabela_no_detalhe(self):
        t = self.tabela(nome="Atacado SP")
        p = self.pedido()
        self.client.force_login(self.vendedor)
        self.assertContains(self.client.get(reverse("vendas:pedidovenda_detail", args=[p.pk])), "Atacado SP")
