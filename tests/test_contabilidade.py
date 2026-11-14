import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.compras.models import ItemPedido, PedidoCompra
from apps.contabilidade import servicos
from apps.contabilidade.models import ContaContabil, Lancamento, PeriodoFechado
from apps.contabilidade.views import resultado
from apps.core.models import Cliente, Fornecedor
from apps.estoque import models as est
from apps.estoque.models import Armazem
from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber, Movimentacao, PlanoConta
from apps.fiscal.models import ItemNota, NotaFiscal, Produto
from apps.vendas.models import ItemPedidoVenda, PedidoVenda


class ContabilidadeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.contador = User.objects.create_user("contador", password="x")
        cls.contador.groups.add(Group.objects.get(name="Contabilidade"))
        cls.banco = ContaBancaria.objects.create(nome="Banco")
        cls.forn = Fornecedor.objects.create(nome="Forn")
        cls.cli = Cliente.objects.create(nome="Cli")
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.prod = Produto.objects.create(codigo="P", descricao="Produto", preco=200, aliquota_icms=18)
        cls.hoje = timezone.localdate()

    def saldo(self, codigo):
        c = ContaContabil.objects.get(codigo=codigo)
        d, cr = servicos.saldos().get(c.pk, (D(0), D(0)))
        return servicos.saldo_natural(c, d, cr)

    def fecha(self):
        tot = servicos.saldos()
        self.assertEqual(sum(d for d, _ in tot.values()), sum(c for _, c in tot.values()))
        ativo = self.saldo("1.1.1") + self.saldo("1.1.2") + self.saldo("1.1.3")
        passivo = self.saldo("2.1.1") + self.saldo("2.1.2") + self.saldo("2.1.3") + self.saldo("2.3.1")
        self.assertEqual(ativo, passivo + resultado(None, self.hoje + datetime.timedelta(days=400))["resultado"])

    def test_ciclo_de_compra_com_estoque_servico_e_pagamento(self):
        pc = PedidoCompra.objects.create(fornecedor=self.forn, status="aprovado", condicao_pagamento="30/60")
        i1 = ItemPedido.objects.create(pedido=pc, produto=self.prod, quantidade=10, valor_unitario=100)
        i2 = ItemPedido.objects.create(pedido=pc, descricao="Frete e instalação", quantidade=1, valor_unitario=50)
        nota = NotaFiscal.objects.create(tipo="entrada", fornecedor=self.forn, numero=77, status="emitida")
        ItemNota.objects.create(nota=nota, produto=self.prod, quantidade=10, valor_unitario=100)
        ItemNota.objects.create(nota=nota, descricao="Frete e instalação", quantidade=1, valor_unitario=50)
        nota.recalcular()
        rec = pc.receber({str(i1.pk): 10, str(i2.pk): 1}, nota_fiscal=nota)
        self.assertEqual(rec.conferencia, "ok", rec.divergencias)
        self.assertEqual(self.saldo("1.1.3"), D("1000"))   # estoques
        self.assertEqual(self.saldo("4.2"), D("50"))       # serviço direto na despesa
        self.assertEqual(self.saldo("2.1.1"), D("1050"))   # fornecedores, em 2 parcelas
        self.assertEqual(self.saldo("2.1.2"), D("0"))      # transitória zerada: nota = recebido
        self.assertFalse(Lancamento.objects.filter(estorno_de__isnull=False).exists())  # sem vai-e-volta
        conta = ContaPagar.objects.order_by("parcela").first()
        conta.baixar(conta.valor, self.hoje, self.banco)
        self.assertEqual((self.saldo("2.1.1"), self.saldo("1.1.1")), (D("525"), D("-525")))
        self.fecha()

    def test_venda_gera_receita_impostos_e_cmv(self):
        est.entrada(self.prod, 10, "120", self.a1, origem="compra")
        pv = PedidoVenda.objects.create(cliente=self.cli, condicao_pagamento="30/60")
        ItemPedidoVenda.objects.create(pedido=pv, produto=self.prod, quantidade=3)
        pv.recalcular()
        pv.confirmar()
        pv.faturar()
        r = resultado(self.hoje.replace(day=1), self.hoje)
        self.assertEqual((r["t"]["receita"], r["t"]["deducao"], r["t"]["custo"]), (D("600"), D("108"), D("360")))
        self.assertEqual(r["resultado"], D("132"))
        self.assertEqual(self.saldo("1.1.2"), D("600"))
        cr = ContaReceber.objects.first()
        cr.baixar(cr.valor, self.hoje, self.banco)
        self.assertEqual((self.saldo("1.1.2"), self.saldo("1.1.1")), (D("300"), D("300")))
        self.fecha()

    def test_titulo_categoria_alteracao_juros_estorno_e_cancelamento(self):
        aluguel = ContaContabil.objects.create(codigo="4.2.1", nome="Aluguel", natureza="despesa",
                                               superior=ContaContabil.objects.get(codigo="4"))
        cat = PlanoConta.objects.create(codigo="9.9", nome="Aluguel", tipo="despesa", conta_contabil=aluguel)
        t = ContaPagar.objects.create(fornecedor=self.forn, descricao="Aluguel", valor=1000, plano_conta=cat,
                                      vencimento=self.hoje)
        self.assertEqual(self.saldo("4.2.1"), D("1000"))
        t.valor = 1200
        t.save()   # corrige: estorna e relança
        self.assertEqual((self.saldo("4.2.1"), self.saldo("2.1.1")), (D("1200"), D("1200")))
        t.juros = 30
        t.save()
        t.baixar(1230, self.hoje, self.banco)
        self.assertEqual((self.saldo("2.1.1"), self.saldo("4.5"), self.saldo("1.1.1")), (D("0"), D("30"), D("-1230")))
        t.estornar()
        self.assertEqual((self.saldo("2.1.1"), self.saldo("4.5"), self.saldo("1.1.1")), (D("1200"), D("0"), D("0")))
        t.cancelar()
        self.assertEqual((self.saldo("2.1.1"), self.saldo("4.2.1")), (D("0"), D("0")))
        self.fecha()

    def test_estoque_consumo_inventario_e_transferencia_bancaria(self):
        est.entrada(self.prod, 10, "10", self.a1, origem="ajuste")
        est.saida(self.prod, 4, self.a1, origem="consumo")
        est.ajustar(self.prod, self.a1, 5, origem="inventario")
        self.assertEqual((self.saldo("1.1.3"), self.saldo("4.3"), self.saldo("4.4")), (D("50"), D("40"), D("-90")))
        Movimentacao.objects.create(conta_bancaria=self.banco, tipo="saida", valor=10, descricao="T",
                                    transferencia=True)
        Movimentacao.objects.create(conta_bancaria=self.banco, tipo="saida", valor=7, descricao="Tarifa")
        self.assertEqual((self.saldo("4.7"), self.saldo("1.1.1")), (D("7"), D("-7")))
        self.fecha()

    def test_periodo_fechado_manual_estorno_e_sintetica(self):
        mes_passado = self.hoje.replace(day=1) - datetime.timedelta(days=1)
        PeriodoFechado.objects.create(ano=mes_passado.year, mes=mes_passado.month)
        caixa, capital = (ContaContabil.objects.get(codigo=c) for c in ("1.1.1", "2.3.1"))
        with self.assertRaisesMessage(ValidationError, "fechado"):
            servicos.gravar(mes_passado, "Capital", caixa, capital, 100, manual=True)
        with self.assertRaisesMessage(ValidationError, "sintética"):
            servicos.gravar(self.hoje, "x", ContaContabil.objects.get(codigo="1"), capital, 1, manual=True)
        t = ContaPagar.objects.create(descricao="Atrasada", valor=80, vencimento=self.hoje, data_emissao=mes_passado)
        lanc = servicos.ativos_de(t).get()
        self.assertEqual(lanc.data, self.hoje.replace(day=1))
        self.assertIn("competência", lanc.historico)
        man = servicos.gravar(self.hoje, "Integralização", caixa, capital, 5000, manual=True)
        est_ = man.estornar()
        self.assertEqual((man.situacao, est_.situacao, self.saldo("2.3.1")), ("Estornado", "Estorno", D("0")))
        with self.assertRaises(ValidationError):
            man.estornar()

    def test_telas_e_reconstrucao(self):
        ContaPagar.objects.create(fornecedor=self.forn, descricao="Luz", valor=300, vencimento=self.hoje)
        self.client.force_login(self.contador)
        for nome in ("balancete", "dre", "balanco", "razao", "periodos", "lancar_manual", "lancamento_list",
                     "contacontabil_list", "regracontabil_list"):
            self.assertEqual(self.client.get(reverse(f"contabilidade:{nome}")).status_code, 200, nome)
        self.assertContains(self.client.get(reverse("contabilidade:balancete")), "Fornecedores")
        c = ContaContabil.objects.get(codigo="2.1.1")
        r = self.client.get(reverse("contabilidade:razao"), {"conta": c.pk, "inicio": self.hoje, "fim": self.hoje})
        self.assertContains(r, "Luz")
        r = self.client.post(reverse("contabilidade:lancar_manual"), {
            "data": self.hoje, "historico": "Capital", "debito": ContaContabil.objects.get(codigo="1.1.1").pk,
            "credito": ContaContabil.objects.get(codigo="2.3.1").pk, "valor": "1000"})
        lanc = Lancamento.objects.get(evento="")
        self.assertRedirects(r, lanc.get_absolute_url())
        self.client.post(reverse("contabilidade:estornar", args=[lanc.pk]))
        self.assertEqual(lanc.partidas.count(), 2)
        self.assertEqual(self.client.get(f"/contabilidade/lancamento/{lanc.pk}/editar/").status_code, 403)
        n = Lancamento.objects.count()
        call_command("contabilizar_historico", verbosity=0)   # nada a refazer
        self.assertEqual(Lancamento.objects.count(), n)
        Lancamento.objects.filter(evento="titulo_pagar").delete()
        call_command("contabilizar_historico", verbosity=0)
        self.assertEqual(self.saldo("2.1.1"), D("300"))
        outro = User.objects.create_user("outro", password="x")
        outro.groups.add(Group.objects.get(name="Usuários"))
        self.client.force_login(outro)
        self.assertEqual(self.client.get(reverse("contabilidade:balancete")).status_code, 403)
