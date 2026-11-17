import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.bancario import models as banc
from apps.contabilidade import servicos
from apps.contabilidade.models import ContaContabil, Lancamento
from apps.core.models import Fornecedor
from apps.financeiro.models import (CentroCusto, ContaBancaria, ContaPagar, ContaReceber, GuiaTributo,
                                    Movimentacao, RegraRetencao, Retencao, definir_rateio, partes_por_centro)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.banco = ContaBancaria.objects.create(nome="Banco")
        cls.forn = Fornecedor.objects.create(nome="Serviços Ltda")
        cls.hoje = timezone.localdate()
        cls.irrf = RegraRetencao.objects.get(tributo="IRRF")
        cls.pis = RegraRetencao.objects.get(tributo="PIS")
        cls.cofins = RegraRetencao.objects.get(tributo="COFINS")

    def titulo(self, valor="1000.00", **kw):
        return ContaPagar.objects.create(fornecedor=self.forn, descricao="Serviço", valor=D(valor),
                                         vencimento=self.hoje, **kw)

    def reter(self, t, *regras, base=None):
        base = base or t.valor
        for r in regras:
            Retencao.objects.create(conta_pagar=t, regra=r, base=base, aliquota=r.aliquota,
                                    valor=(base * r.aliquota / 100).quantize(D("0.01")))

    def saldo(self, codigo):
        c = ContaContabil.objects.get(codigo=codigo)
        d, cr = servicos.saldos().get(c.pk, (D(0), D(0)))
        return servicos.saldo_natural(c, d, cr)


class RetencaoTest(Base):
    def test_regras_padrao_criadas(self):
        self.assertEqual(RegraRetencao.objects.count(), 4)
        self.assertEqual(self.irrf.aliquota, D("1.5"))

    def test_baixa_total_paga_o_liquido_e_quita_o_titulo(self):
        t = self.titulo()
        self.reter(t, self.irrf, self.pis)        # 15,00 + 6,50
        self.assertEqual(t.saldo_liquido, D("978.50"))
        mov = t.baixar(D("1000"), self.hoje, self.banco)
        t.refresh_from_db()
        self.assertEqual(mov.valor, D("978.50"))
        self.assertEqual(t.status, "pago")
        self.assertEqual(t.valor_pago, D("1000"))
        self.assertEqual(self.banco.saldo_atual, D("-978.50"))

    def test_baixa_cria_guia_por_codigo_de_receita(self):
        t = self.titulo()
        self.reter(t, self.irrf, self.pis, self.cofins)
        t.baixar(D("1000"), self.hoje, self.banco)
        guias = {g.codigo_receita: g for g in GuiaTributo.objects.all()}
        self.assertEqual(set(guias), {"1708", "5952"})
        self.assertEqual(guias["1708"].valor, D("15.00"))
        self.assertEqual(guias["5952"].valor, D("36.50"))    # PIS 6,50 + COFINS 30,00
        self.assertEqual(guias["1708"].status, "aberta")
        comp = self.hoje.replace(day=1)
        self.assertEqual(guias["1708"].competencia, comp)
        self.assertEqual(guias["1708"].vencimento.day, 20)
        self.assertGreater(guias["1708"].vencimento, comp + datetime.timedelta(days=27))

    def test_baixa_parcial_e_retencao_proporcional_sem_perder_centavo(self):
        t = self.titulo("1000.00")
        self.reter(t, self.irrf, self.pis, self.cofins)   # total 15 + 6,50 + 30 = 51,50
        m1 = t.baixar(D("333.33"), self.hoje, self.banco)
        m2 = t.baixar(D("333.33"), self.hoje, self.banco)
        m3 = t.baixar(t.saldo, self.hoje, self.banco)
        t.refresh_from_db()
        self.assertEqual(t.status, "pago")
        retido = sum(r.retido for r in t.retencoes.all())
        self.assertEqual(retido, D("51.50"))
        self.assertEqual(m1.valor + m2.valor + m3.valor + retido, D("1000.00"))
        self.assertEqual(sum(g.valor for g in GuiaTributo.objects.all()), D("51.50"))

    def test_baixa_parcial_nao_deixa_guia_diferente_do_retido(self):
        t = self.titulo("100.00")
        self.reter(t, self.irrf)
        t.baixar(D("40"), self.hoje, self.banco)
        self.assertEqual(GuiaTributo.objects.get().valor, D("0.60"))

    def test_titulo_sem_retencao_continua_igual(self):
        t = self.titulo()
        mov = t.baixar(D("400"), self.hoje, self.banco)
        self.assertEqual(mov.valor, D("400"))
        self.assertFalse(GuiaTributo.objects.exists())
        self.assertEqual(t.saldo_liquido, D("600"))

    def test_contas_a_receber_nao_retem(self):
        r = ContaReceber.objects.create(descricao="Venda", valor=D("100"), vencimento=self.hoje)
        mov = r.baixar(D("100"), self.hoje, self.banco)
        self.assertEqual(mov.valor, D("100"))
        self.assertEqual(r.saldo_liquido, r.saldo)

    def test_contabilidade_fecha_com_retencao_e_recolhimento(self):
        t = self.titulo()
        self.reter(t, self.irrf, self.pis, self.cofins)
        t.baixar(D("1000"), self.hoje, self.banco)
        # fornecedor quitado, banco pagou o líquido e o Fisco virou obrigação
        self.assertEqual(self.saldo("2.1.1"), D("0"))
        self.assertEqual(self.saldo("1.1.1"), D("-948.50"))
        self.assertEqual(self.saldo("2.1.3"), D("51.50"))
        for g in GuiaTributo.objects.all():
            g.recolher(self.banco, self.hoje)
        self.assertEqual(self.saldo("2.1.3"), D("0"))
        self.assertEqual(self.saldo("1.1.1"), D("-1000.00"))
        tot = servicos.saldos()
        self.assertEqual(sum(d for d, _ in tot.values()), sum(c for _, c in tot.values()))

    def test_juros_e_desconto_reconhecidos_na_quitacao_com_retencao(self):
        t = self.titulo(juros=D("10"))
        self.reter(t, self.irrf)
        t.baixar(D("1010"), self.hoje, self.banco)
        self.assertTrue(Lancamento.objects.filter(evento="juros_pagos").exists())
        self.assertEqual(self.saldo("2.1.1"), D("0"))

    def test_recolher_guia_gera_saida_e_nao_pode_repetir(self):
        t = self.titulo()
        self.reter(t, self.irrf)
        t.baixar(D("1000"), self.hoje, self.banco)
        g = GuiaTributo.objects.get()
        g.recolher(self.banco, self.hoje)
        g.refresh_from_db()
        self.assertEqual(g.status, "paga")
        self.assertEqual(self.banco.saldo_atual, D("-1000"))
        with self.assertRaises(ValidationError):
            g.recolher(self.banco, self.hoje)

    def test_estornar_recolhimento_reabre_a_guia(self):
        t = self.titulo()
        self.reter(t, self.irrf)
        t.baixar(D("1000"), self.hoje, self.banco)
        g = GuiaTributo.objects.get()
        g.recolher(self.banco, self.hoje)
        g.estornar_recolhimento()
        g.refresh_from_db()
        self.assertEqual(g.status, "aberta")
        self.assertEqual(self.saldo("2.1.3"), D("15.00"))
        self.assertEqual(self.banco.saldo_atual, D("-985.00"))

    def test_estornar_pagamento_desfaz_retencao_e_guia(self):
        t = self.titulo()
        self.reter(t, self.irrf, self.pis)
        t.baixar(D("1000"), self.hoje, self.banco)
        t.estornar()
        t.refresh_from_db()
        self.assertEqual(t.status, "aberto")
        self.assertFalse(GuiaTributo.objects.exists())
        self.assertEqual(sum(r.retido for r in t.retencoes.all()), D("0"))
        self.assertEqual(self.saldo("2.1.3"), D("0"))
        self.assertEqual(self.saldo("2.1.1"), D("1000"))   # provisão volta a valer
        self.assertEqual(t.saldo_liquido, D("978.50"))

    def test_estorno_bloqueado_com_guia_recolhida(self):
        t = self.titulo()
        self.reter(t, self.irrf)
        t.baixar(D("1000"), self.hoje, self.banco)
        GuiaTributo.objects.get().recolher(self.banco, self.hoje)
        with self.assertRaises(ValidationError):
            t.estornar()
        t.refresh_from_db()
        self.assertEqual(t.status, "pago")

    def test_estorno_parcial_recalcula_guia(self):
        t = self.titulo("200.00")
        self.reter(t, self.irrf)                    # 3,00
        t.baixar(D("100"), self.hoje, self.banco)    # retém 1,50
        self.assertEqual(GuiaTributo.objects.get().valor, D("1.50"))
        t.baixar(D("100"), self.hoje, self.banco)    # retém mais 1,50 na mesma guia
        self.assertEqual(GuiaTributo.objects.get().valor, D("3.00"))
        t.estornar()
        self.assertFalse(GuiaTributo.objects.exists())

    def test_guia_recolhida_nao_recebe_nova_retencao(self):
        t = self.titulo("200.00")
        self.reter(t, self.irrf)
        t.baixar(D("100"), self.hoje, self.banco)
        GuiaTributo.objects.get().recolher(self.banco, self.hoje)
        t.baixar(D("100"), self.hoje, self.banco)
        self.assertEqual(GuiaTributo.objects.count(), 2)
        self.assertEqual(GuiaTributo.objects.filter(status="aberta").get().valor, D("1.50"))

    def test_bruto_de_liquido(self):
        t = self.titulo("1000.00")
        self.reter(t, self.irrf)           # 15,00
        self.assertEqual(t.bruto_de_liquido(D("985.00")), D("1000.00"))
        self.assertEqual(t.bruto_de_liquido(D("492.50")), D("500.00"))

    def test_conciliacao_de_extrato_quita_pelo_liquido(self):
        t = self.titulo("1000.00")
        self.reter(t, self.irrf)
        ext = banc.Extrato.objects.create(conta_bancaria=self.banco, arquivo="x.ofx", inicio=self.hoje,
                                          fim=self.hoje)
        linha = banc.LinhaExtrato.objects.create(extrato=ext, conta_bancaria=self.banco, data=self.hoje,
                                                 valor=D("-985.00"), descricao="PGTO", fitid="1")
        self.assertIn(t, linha.titulos_sugeridos())
        linha.baixar_titulo(t)
        t.refresh_from_db()
        self.assertEqual(t.status, "pago")
        self.assertEqual(self.banco.saldo_atual, D("-985.00"))
        self.assertEqual(GuiaTributo.objects.get().valor, D("15.00"))

    def test_bordero_paga_o_liquido(self):
        forn = Fornecedor.objects.create(nome="F2", documento="11444777000161", banco="237 Bradesco",
                                         agencia="0456-7", conta="12345-6")
        t = ContaPagar.objects.create(fornecedor=forn, descricao="NF", valor=D("1000"), vencimento=self.hoje,
                                      aprovado=True)
        self.reter(t, self.irrf)
        b = banc.Bordero.objects.create(conta_bancaria=self.banco, data_pagamento=self.hoje)
        b.adicionar([t])
        self.assertEqual(b.itens.get().valor, D("985.00"))


class TelasRetencaoTest(Base):
    def setUp(self):
        self.client.force_login(self.fin)

    def test_calcular_retencoes_pela_tela(self):
        t = self.titulo("1000.00")
        r = self.client.post(reverse("financeiro:titulo_retencoes", args=[t.pk]),
                             {"base": "1000.00", "regras": [self.irrf.pk, self.pis.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(t.retencoes_previstas, D("21.50"))
        # refazer substitui
        self.client.post(reverse("financeiro:titulo_retencoes", args=[t.pk]), {"base": "500.00",
                                                                               "regras": [self.irrf.pk]})
        self.assertEqual(t.retencoes_previstas, D("7.50"))
        self.assertEqual(t.retencoes.count(), 1)

    def test_nao_calcula_depois_de_pagar(self):
        t = self.titulo()
        t.baixar(D("100"), self.hoje, self.banco)
        self.client.post(reverse("financeiro:titulo_retencoes", args=[t.pk]), {"base": "1000", "regras": [self.irrf.pk]})
        self.assertEqual(t.retencoes.count(), 0)

    def test_base_maior_que_o_titulo_e_recusada(self):
        t = self.titulo("100.00")
        self.client.post(reverse("financeiro:titulo_retencoes", args=[t.pk]), {"base": "500", "regras": [self.irrf.pk]})
        self.assertEqual(t.retencoes.count(), 0)

    def test_base_minima_dispensa_retencao(self):
        self.irrf.base_minima = D("2000")
        self.irrf.save()
        t = self.titulo("1000.00")
        self.client.post(reverse("financeiro:titulo_retencoes", args=[t.pk]), {"base": "1000", "regras": [self.irrf.pk]})
        self.assertEqual(t.retencoes.count(), 0)

    def test_tela_de_baixa_avisa_da_retencao(self):
        t = self.titulo()
        self.reter(t, self.irrf)
        r = self.client.get(reverse("financeiro:titulo_baixar_pagar", args=[t.pk]))
        self.assertContains(r, "tributos retidos na fonte")

    def test_pagar_pela_tela_e_recolher_guia(self):
        t = self.titulo()
        self.reter(t, self.irrf)
        r = self.client.post(reverse("financeiro:titulo_baixar_pagar", args=[t.pk]),
                             {"valor": "1000.00", "data": self.hoje.isoformat(), "conta_bancaria": self.banco.pk})
        self.assertEqual(r.status_code, 302)
        g = GuiaTributo.objects.get()
        self.assertEqual(self.client.get(reverse("financeiro:guiatributo_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:guiatributo_detail", args=[g.pk])).status_code, 200)
        r = self.client.post(reverse("financeiro:guia_recolher", args=[g.pk]),
                             {"conta_bancaria": self.banco.pk, "data": self.hoje.isoformat()})
        g.refresh_from_db()
        self.assertEqual(g.status, "paga")
        r = self.client.post(reverse("financeiro:titulo_estornar_pagar", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual(t.status, "pago")           # bloqueado: guia recolhida

    def test_guias_somente_leitura(self):
        t = self.titulo()
        self.reter(t, self.irrf)
        t.baixar(D("1000"), self.hoje, self.banco)
        g = GuiaTributo.objects.get()
        self.assertEqual(self.client.get(reverse("financeiro:guiatributo_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("financeiro:guiatributo_update", args=[g.pk])).status_code, 403)

    def test_sem_permissao_nao_calcula_nem_recolhe(self):
        u = User.objects.create_user("leitor", password="x")
        u.groups.add(Group.objects.get(name="Auditoria"))
        t = self.titulo()
        self.client.force_login(u)
        self.client.post(reverse("financeiro:titulo_retencoes", args=[t.pk]), {"base": "1000", "regras": [self.irrf.pk]})
        self.assertEqual(t.retencoes.count(), 0)

    def test_regra_de_retencao_valida_aliquota(self):
        r = RegraRetencao(tributo="IRRF", nome="X", aliquota=D("150"), dia_vencimento=20)
        with self.assertRaises(ValidationError):
            r.full_clean()


class RateioTest(Base):
    def setUp(self):
        self.a = CentroCusto.objects.create(codigo="A", nome="Administrativo")
        self.b = CentroCusto.objects.create(codigo="B", nome="Comercial")
        self.c = CentroCusto.objects.create(codigo="C", nome="Produção")

    def test_partes_somam_o_valor_exato(self):
        t = self.titulo("100.00")
        definir_rateio(t, [(self.a, D("33.33")), (self.b, D("33.33")), (self.c, D("33.34"))])
        partes = partes_por_centro(t)
        self.assertEqual(sum(v for _, v in partes), D("100.00"))
        self.assertEqual([c for c, _ in partes], [self.a, self.b, self.c])

    def test_sem_rateio_vai_tudo_para_o_centro_do_titulo(self):
        t = self.titulo("100.00", centro_custo=self.a)
        self.assertEqual(partes_por_centro(t), [(self.a, D("100.00"))])

    def test_exige_100_por_cento(self):
        t = self.titulo()
        with self.assertRaises(ValidationError):
            definir_rateio(t, [(self.a, D("60")), (self.b, D("30"))])
        self.assertEqual(t.rateios.count(), 0)

    def test_centro_repetido_e_percentual_invalido(self):
        t = self.titulo()
        with self.assertRaises(ValidationError):
            definir_rateio(t, [(self.a, D("50")), (self.a, D("50"))])
        with self.assertRaises(ValidationError):
            definir_rateio(t, [(self.a, D("110")), (self.b, D("-10"))])

    def test_redefinir_substitui_e_vazio_remove(self):
        t = self.titulo()
        definir_rateio(t, [(self.a, D("100"))])
        definir_rateio(t, [(self.b, D("40")), (self.c, D("60"))])
        self.assertEqual(t.rateios.count(), 2)
        definir_rateio(t, [])
        self.assertEqual(t.rateios.count(), 0)

    def test_rateio_de_conta_a_receber(self):
        r = ContaReceber.objects.create(descricao="Venda", valor=D("200"), vencimento=self.hoje)
        definir_rateio(r, [(self.a, D("25")), (self.b, D("75"))])
        self.assertEqual([v for _, v in partes_por_centro(r)], [D("50.00"), D("150.00")])

    def test_tela_de_rateio_e_relatorio(self):
        self.client.force_login(self.fin)
        t = self.titulo("1000.00")
        url = reverse("financeiro:titulo_ratear_pagar", args=[t.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        dados = {"form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0",
                 "form-MAX_NUM_FORMS": "1000", "form-0-centro_custo": self.a.pk, "form-0-percentual": "70",
                 "form-1-centro_custo": self.b.pk, "form-1-percentual": "30"}
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        self.assertEqual(t.rateios.count(), 2)
        r = self.client.get(reverse("financeiro:relatorio_centro_custo"), {"de": self.hoje.isoformat(),
                                                                          "ate": self.hoje.isoformat()})
        self.assertContains(r, "Administrativo")
        self.assertContains(r, "700,00")
        self.assertContains(r, "300,00")
        # soma errada não grava
        dados["form-1-percentual"] = "20"
        self.client.post(url, dados)
        self.assertEqual(sorted(t.rateios.values_list("percentual", flat=True)), [D("30.00"), D("70.00")])

    def test_relatorio_ignora_cancelados_e_mostra_sem_centro(self):
        self.client.force_login(self.fin)
        t = self.titulo("50.00")
        c = self.titulo("999.00")
        c.cancelar()
        r = self.client.get(reverse("financeiro:relatorio_centro_custo"), {"de": self.hoje.isoformat(),
                                                                          "ate": self.hoje.isoformat()})
        self.assertContains(r, "Sem centro de custo")
        self.assertNotContains(r, "999,00")
