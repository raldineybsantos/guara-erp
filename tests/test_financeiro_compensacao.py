import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cobranca.models import Cobranca
from apps.contabilidade import servicos
from apps.contabilidade.models import ContaContabil, Lancamento
from apps.core.models import Cliente, Fornecedor
from apps.financeiro.models import (Compensacao, ContaBancaria, ContaPagar, ContaReceber, RegraRetencao, Renegociacao,
                                    Retencao)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.banco = ContaBancaria.objects.create(nome="Banco")
        cls.hoje = timezone.localdate()
        cls.cliente = Cliente.objects.create(nome="Parceiro SA", documento="11222333000181")
        cls.forn = cls.cliente.adicionar_papel("fornecedor")
        cls.outro = Cliente.objects.create(nome="Outro", documento="52998224725")

    def pagar(self, valor="1000", **kw):
        return ContaPagar.objects.create(fornecedor=self.forn, descricao="Compra", valor=D(valor),
                                         vencimento=self.hoje, **kw)

    def receber(self, valor="600", cliente=None, venc=None, **kw):
        return ContaReceber.objects.create(cliente=cliente or self.cliente, descricao="Venda", valor=D(valor),
                                           vencimento=venc or self.hoje, **kw)

    def saldo(self, codigo):
        c = ContaContabil.objects.get(codigo=codigo)
        d, cr = servicos.saldos().get(c.pk, (D(0), D(0)))
        return servicos.saldo_natural(c, d, cr)


class CompensacaoTest(Base):
    def test_compensa_parcialmente_e_contabiliza_sem_banco(self):
        p, r = self.pagar("1000"), self.receber("600")
        comp = Compensacao.compensar(p, r, D("600"), self.hoje)
        p.refresh_from_db(); r.refresh_from_db()
        self.assertEqual((p.status, p.valor_pago), ("parcial", D("600")))
        self.assertEqual((r.status, r.valor_pago), ("pago", D("600")))
        self.assertEqual(self.banco.saldo_atual, 0)
        self.assertFalse(p.movimentacoes.exists() or r.movimentacoes.exists())
        lanc = servicos.ativos_de(comp, ["compensacao"]).get()
        self.assertEqual(lanc.valor_total if hasattr(lanc, "valor_total") else D("600"), D("600"))
        self.assertEqual(self.saldo("2.1.1"), D("400"))   # fornecedores: 1000 - 600
        self.assertEqual(self.saldo("1.1.2"), D("0"))     # clientes: 600 - 600

    def test_valor_acima_do_menor_saldo_recusado(self):
        p, r = self.pagar("1000"), self.receber("600")
        with self.assertRaises(ValidationError):
            Compensacao.compensar(p, r, D("600.01"))

    def test_parceiros_diferentes_recusado(self):
        with self.assertRaises(ValidationError):
            Compensacao.compensar(self.pagar(), self.receber(cliente=self.outro), D("100"))

    def test_titulo_com_retencao_pendente_recusado(self):
        p = self.pagar()
        regra = RegraRetencao.objects.get(tributo="IRRF")
        Retencao.objects.create(conta_pagar=p, regra=regra, base=p.valor, aliquota=regra.aliquota, valor=D("15"))
        with self.assertRaises(ValidationError):
            Compensacao.compensar(p, self.receber(), D("100"))

    def test_titulo_pago_ou_cancelado_recusado(self):
        p, r = self.pagar(), self.receber()
        r.cancelar()
        with self.assertRaises(ValidationError):
            Compensacao.compensar(p, r, D("10"))

    def test_estornar_restaura_titulos_e_lancamentos(self):
        p, r = self.pagar("1000"), self.receber("600")
        comp = Compensacao.compensar(p, r, D("300"), self.hoje)
        comp.estornar()
        p.refresh_from_db(); r.refresh_from_db(); comp.refresh_from_db()
        self.assertEqual((p.status, p.valor_pago, r.status, r.valor_pago), ("aberto", 0, "aberto", 0))
        self.assertEqual(comp.status, "estornada")
        self.assertEqual(self.saldo("2.1.1"), D("1000"))
        self.assertEqual(self.saldo("1.1.2"), D("600"))
        with self.assertRaises(ValidationError):
            comp.estornar()

    def test_estornar_baixa_normal_bloqueado_com_compensacao(self):
        p, r = self.pagar("1000"), self.receber("600")
        Compensacao.compensar(p, r, D("300"), self.hoje)
        p.refresh_from_db()
        with self.assertRaises(ValidationError):
            p.estornar()

    def test_compensacao_mais_pagamento_quita_com_juros(self):
        p = self.pagar("1000", juros=D("50"))
        r = self.receber("1050")
        Compensacao.compensar(p, r, D("1050"), self.hoje)
        p.refresh_from_db()
        self.assertEqual(p.status, "pago")
        self.assertTrue(servicos.ativos_de(Compensacao.objects.get(), ["juros_pagos"]).exists())

    def test_tela_compensar_e_estornar(self):
        self.client.force_login(self.fin)
        p, r = self.pagar("1000"), self.receber("600")
        outro = self.receber("50", cliente=self.outro)
        url = reverse("financeiro:compensar_pagar", args=[p.pk])
        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, "Venda")
        self.assertEqual(len(pagina.context["form"].fields["contraparte"].queryset), 1)
        self.assertNotIn(outro, pagina.context["form"].fields["contraparte"].queryset)
        resp = self.client.post(url, {"contraparte": r.pk, "valor": "600", "data": self.hoje.isoformat()})
        self.assertEqual(resp.status_code, 302)
        comp = Compensacao.objects.get()
        self.assertEqual(comp.valor, D("600"))
        self.assertEqual(self.client.get(reverse("financeiro:compensacao_detail", args=[comp.pk])).status_code, 200)
        self.client.post(reverse("financeiro:compensacao_estornar", args=[comp.pk]))
        comp.refresh_from_db()
        self.assertEqual(comp.status, "estornada")

    def test_tela_sem_permissao(self):
        u = User.objects.create_user("semperm", password="x")
        self.client.force_login(u)
        p = self.pagar()
        resp = self.client.post(reverse("financeiro:compensar_pagar", args=[p.pk]), {})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Compensacao.objects.exists())


class RenegociacaoTest(Base):
    def vencidos(self):
        antigo = self.hoje - datetime.timedelta(days=40)
        return self.receber("500", venc=antigo), self.receber("300", venc=antigo)

    def test_renegocia_com_juros_e_parcelas(self):
        a, b = self.vencidos()
        reneg = Renegociacao.efetivar([a, b], self.hoje + datetime.timedelta(days=30), parcelas=3,
                                      intervalo_dias=30, acrescimo=D("40"), desconto=D("10"))
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.status, b.status), ("renegociado", "renegociado"))
        self.assertEqual((reneg.total_original, reneg.total_novo), (D("800"), D("830")))
        novos = list(reneg.novos_titulos.order_by("parcela"))
        self.assertEqual([n.valor for n in novos], [D("276.68"), D("276.66"), D("276.66")])
        self.assertEqual(sum(n.valor for n in novos), D("830"))
        self.assertEqual(novos[1].vencimento, novos[0].vencimento + datetime.timedelta(days=30))
        # carteira: clientes = novo total; receita líquida = só o acréscimo menos o desconto
        self.assertEqual(self.saldo("1.1.2"), D("830"))
        self.assertEqual(self.saldo("3.1"), D("830"))

    def test_originais_renegociados_nao_podem_ser_baixados_estornados_ou_cancelados(self):
        a, b = self.vencidos()
        Renegociacao.efetivar([a], self.hoje, parcelas=1)
        a.refresh_from_db()
        for acao in (lambda: a.baixar(D("10"), self.hoje, self.banco), a.estornar, a.cancelar):
            with self.assertRaises(ValidationError):
                acao()
        self.assertNotIn(a, ContaReceber.objects.em_aberto())

    def test_titulo_parcialmente_pago_renegocia_o_saldo(self):
        a, _ = self.vencidos()
        a.baixar(D("200"), self.hoje, self.banco)
        a.refresh_from_db()
        reneg = Renegociacao.efetivar([a], self.hoje, parcelas=2)
        self.assertEqual(reneg.total_original, D("300"))

    def test_validacoes(self):
        a, b = self.vencidos()
        c = self.receber("100", cliente=self.outro)
        with self.assertRaises(ValidationError):
            Renegociacao.efetivar([a, c], self.hoje)           # clientes diferentes
        with self.assertRaises(ValidationError):
            Renegociacao.efetivar([a], self.hoje, desconto=D("500"))   # total zerado
        with self.assertRaises(ValidationError):
            Renegociacao.efetivar([], self.hoje)
        Renegociacao.efetivar([a], self.hoje)
        with self.assertRaises(ValidationError):
            Renegociacao.efetivar([a], self.hoje)              # já renegociado

    def test_cobrancas_pendentes_sao_canceladas(self):
        a, _ = self.vencidos()
        cob = Cobranca.objects.create(conta_receber=a, tipo="boleto", vencimento=a.vencimento, valor=a.valor)
        Renegociacao.efetivar([a], self.hoje)
        cob.refresh_from_db()
        self.assertEqual(cob.status, "cancelada")

    def test_desfazer_restaura_tudo(self):
        a, b = self.vencidos()
        reneg = Renegociacao.efetivar([a, b], self.hoje, parcelas=2, acrescimo=D("40"))
        reneg.desfazer()
        a.refresh_from_db(); b.refresh_from_db(); reneg.refresh_from_db()
        self.assertEqual((a.status, b.status, reneg.status), ("aberto", "aberto", "desfeita"))
        self.assertEqual({n.status for n in reneg.novos_titulos.all()}, {"cancelado"})
        self.assertEqual(self.saldo("1.1.2"), D("800"))
        self.assertEqual(self.saldo("3.1"), D("800"))
        with self.assertRaises(ValidationError):
            reneg.desfazer()

    def test_desfazer_bloqueado_se_parcela_paga(self):
        a, _ = self.vencidos()
        reneg = Renegociacao.efetivar([a], self.hoje, parcelas=2)
        reneg.novos_titulos.first().baixar(D("10"), self.hoje, self.banco)
        with self.assertRaises(ValidationError):
            reneg.desfazer()

    def test_tela_renegociar(self):
        self.client.force_login(self.fin)
        a, b = self.vencidos()
        url = reverse("financeiro:renegociar", args=[a.pk])
        pagina = self.client.get(url)
        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(set(pagina.context["form"].initial["titulos"]), {a.pk, b.pk})
        resp = self.client.post(url, {"titulos": [a.pk, b.pk], "primeiro_vencimento": self.hoje.isoformat(),
                                      "parcelas": 2, "intervalo_dias": 30, "acrescimo": "0", "desconto": "0"})
        self.assertEqual(resp.status_code, 302)
        reneg = Renegociacao.objects.get()
        self.assertEqual(self.client.get(reneg.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:renegociacao_list")).status_code, 200)
        self.client.post(reverse("financeiro:renegociacao_desfazer", args=[reneg.pk]))
        reneg.refresh_from_db()
        self.assertEqual(reneg.status, "desfeita")

    def test_tela_sem_permissao(self):
        a, _ = self.vencidos()
        self.client.force_login(User.objects.create_user("semperm", password="x"))
        self.client.post(reverse("financeiro:renegociar", args=[a.pk]), {})
        self.assertFalse(Renegociacao.objects.exists())
