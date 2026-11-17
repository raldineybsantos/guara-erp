import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.contabilidade import orcamento as orc_s
from apps.contabilidade import patrimonio, servicos
from apps.contabilidade.models import Bem, ContaContabil, Depreciacao, Lancamento, LinhaOrcamento, Orcamento
from apps.core.models import Fornecedor, Notificacao
from apps.financeiro.forms import ContaPagarForm
from apps.financeiro.models import CentroCusto, ContaBancaria, ContaPagar, ContaReceber, PlanoConta, definir_rateio


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.cont = User.objects.create_user("contador", password="x")
        cls.cont.groups.add(Group.objects.get(name="Contabilidade"))
        cls.hoje = timezone.localdate()

    def saldo(self, codigo):
        c = ContaContabil.objects.get(codigo=codigo)
        d, cr = servicos.saldos().get(c.pk, (D(0), D(0)))
        return servicos.saldo_natural(c, d, cr)

    def fecha(self):
        tot = servicos.saldos()
        self.assertEqual(sum(d for d, _ in tot.values()), sum(c for _, c in tot.values()))


class AtivoFixoTest(Base):
    def bem(self, valor="12000", residual="0", vida=12, ini=None, **kw):
        return Bem.objects.create(codigo=kw.pop("codigo", "B1"), descricao="Máquina", valor_aquisicao=D(valor),
                                  valor_residual=D(residual), vida_util_meses=vida,
                                  data_aquisicao=ini or datetime.date(2026, 1, 15),
                                  contrapartida=ContaContabil.objects.get(codigo="2.3.1"), **kw)

    def test_contas_e_regras_criadas(self):
        for c in ("1.2.1", "1.2.2", "4.8"):
            self.assertTrue(ContaContabil.objects.filter(codigo=c).exists())

    def test_vida_util_sugerida_e_inicio_no_primeiro_dia(self):
        b = Bem.objects.create(codigo="V", descricao="Carro", categoria="veiculos", valor_aquisicao=D("60000"),
                               data_aquisicao=datetime.date(2026, 3, 20))
        self.assertEqual(b.vida_util_meses, 60)
        self.assertEqual(b.inicio_depreciacao, datetime.date(2026, 3, 1))

    def test_aquisicao_contabilizada_pela_contrapartida(self):
        self.bem()
        self.assertEqual(self.saldo("1.2.1"), D("12000"))
        self.assertEqual(self.saldo("2.3.1"), D("12000"))

    def test_sem_contrapartida_nao_contabiliza_aquisicao(self):
        Bem.objects.create(codigo="X", descricao="Mesa", valor_aquisicao=D("500"))
        self.assertFalse(Lancamento.objects.filter(evento="aquisicao_bem").exists())

    def test_depreciacao_linear_ate_zerar_sem_perder_centavo(self):
        b = self.bem(valor="1000", vida=3)           # 333,33 + 333,33 + 333,34
        n = patrimonio.depreciar_ate(datetime.date(2026, 6, 1))
        self.assertEqual(n, 3)
        self.assertEqual(b.depreciacao_acumulada, D("1000.00"))
        self.assertEqual(b.valor_contabil, D("0"))
        self.assertEqual(sorted(b.depreciacoes.values_list("valor", flat=True)), [D("333.33"), D("333.33"), D("333.34")])
        self.assertEqual(self.saldo("1.2.2"), D("-1000.00"))   # redutora: saldo credor dentro do ativo
        self.assertEqual(self.saldo("4.8"), D("1000.00"))
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 12, 1)), 0)
        self.fecha()

    def test_valor_residual_nao_e_depreciado(self):
        b = self.bem(valor="1200", residual="200", vida=10)
        patrimonio.depreciar_ate(datetime.date(2026, 12, 1))
        self.assertEqual(b.depreciacao_acumulada, D("1000.00"))
        self.assertEqual(b.valor_contabil, D("200.00"))

    def test_repetir_nao_duplica_e_recupera_meses_atrasados(self):
        b = self.bem()
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 3, 1)), 3)
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 3, 1)), 0)
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 5, 1)), 2)
        self.assertEqual(b.depreciacoes.count(), 5)

    def test_nao_deprecia_antes_do_inicio_nem_bem_baixado(self):
        b = self.bem(ini=datetime.date(2026, 6, 10))
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 5, 1)), 0)
        patrimonio.depreciar_ate(datetime.date(2026, 7, 1))
        patrimonio.baixar_bem(b, datetime.date(2026, 8, 20))
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 12, 1)), 0)

    def test_lancamento_da_depreciacao_no_ultimo_dia_do_mes_com_centro_de_custo(self):
        cc = CentroCusto.objects.create(codigo="P", nome="Produção")
        self.bem(centro_custo=cc)
        patrimonio.depreciar_ate(datetime.date(2026, 2, 1))
        lanc = Lancamento.objects.filter(evento="depreciacao").order_by("data")
        self.assertEqual([x.data for x in lanc], [datetime.date(2026, 1, 31), datetime.date(2026, 2, 28)])
        self.assertTrue(all(p.centro_custo == cc for x in lanc for p in x.partidas.all()))

    def test_estornar_depreciacao_so_do_mes_mais_recente(self):
        b = self.bem()
        patrimonio.depreciar_ate(datetime.date(2026, 3, 1))
        with self.assertRaises(ValidationError):
            patrimonio.estornar_depreciacao(datetime.date(2026, 2, 1))
        self.assertEqual(patrimonio.estornar_depreciacao(datetime.date(2026, 3, 1)), 1)
        self.assertEqual(b.depreciacoes.count(), 2)
        self.assertEqual(self.saldo("1.2.2"), D("-2000.00"))
        self.assertEqual(patrimonio.depreciar_ate(datetime.date(2026, 3, 1)), 1)   # refaz

    def test_baixa_com_perda(self):
        b = self.bem()
        patrimonio.depreciar_ate(datetime.date(2026, 3, 1))     # acum 3000, contábil 9000
        r = patrimonio.baixar_bem(b, datetime.date(2026, 4, 10), D("5000"), "Venda")
        self.assertEqual(r, D("4000.00"))                       # perda
        b.refresh_from_db()
        self.assertEqual(b.status, "baixado")
        self.assertEqual(self.saldo("1.2.1"), D("0"))
        self.assertEqual(self.saldo("1.2.2"), D("0"))
        self.fecha()

    def test_baixa_depreciando_ate_o_mes_anterior(self):
        b = self.bem()
        patrimonio.baixar_bem(b, datetime.date(2026, 4, 10), D("0"))
        self.assertEqual(b.depreciacoes.count(), 3)             # jan, fev, mar
        self.assertEqual(self.saldo("1.2.1"), D("0"))
        self.assertEqual(self.saldo("1.2.2"), D("0"))
        self.assertEqual(self.saldo("4.7"), D("9000.00"))       # perda do valor contábil
        self.fecha()

    def test_baixa_com_ganho(self):
        b = self.bem()
        patrimonio.baixar_bem(b, datetime.date(2026, 4, 10), D("10000"))   # contábil 9000 → ganho 1000
        self.assertEqual(self.saldo("3.4"), D("1000.00"))
        self.assertEqual(self.saldo("1.2.1"), D("0"))
        self.assertEqual(self.saldo("1.1.1"), D("10000.00"))
        self.fecha()

    def test_baixa_sem_resultado(self):
        b = self.bem()
        patrimonio.baixar_bem(b, datetime.date(2026, 4, 10), D("9000"))
        self.assertEqual(self.saldo("4.7"), D("0"))
        self.assertEqual(self.saldo("3.4"), D("0"))
        self.fecha()

    def test_nao_baixa_duas_vezes_nem_antes_da_aquisicao(self):
        b = self.bem()
        with self.assertRaises(ValidationError):
            patrimonio.baixar_bem(b, datetime.date(2025, 12, 1))
        patrimonio.baixar_bem(b, datetime.date(2026, 2, 1))
        with self.assertRaises(ValidationError):
            patrimonio.baixar_bem(b, datetime.date(2026, 3, 1))

    def test_dados_travados_apos_depreciar_e_bem_baixado(self):
        b = self.bem()
        patrimonio.depreciar_ate(datetime.date(2026, 1, 1))
        b.valor_aquisicao = D("999")
        with self.assertRaises(ValidationError):
            b.full_clean()
        b.refresh_from_db()
        patrimonio.baixar_bem(b, datetime.date(2026, 3, 1))
        b.refresh_from_db()
        b.descricao = "Outra"
        with self.assertRaises(ValidationError):
            b.full_clean()

    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            Bem(codigo="Z", descricao="x", valor_aquisicao=D("100"), valor_residual=D("100")).full_clean()
        with self.assertRaises(ValidationError):
            Bem(codigo="Z", descricao="x", valor_aquisicao=D("0")).full_clean()

    def test_telas_e_comando(self):
        b = self.bem()
        self.client.force_login(self.cont)
        self.assertEqual(self.client.get(reverse("contabilidade:bem_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("contabilidade:bem_detail", args=[b.pk])).status_code, 200)
        r = self.client.post(reverse("contabilidade:depreciar"), {"ano": 2026, "mes": 2, "acao": "depreciar"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(b.depreciacoes.count(), 2)
        r = self.client.post(reverse("contabilidade:depreciar"), {"ano": 2026, "mes": 2, "acao": "estornar"})
        self.assertEqual(b.depreciacoes.count(), 1)
        r = self.client.post(reverse("contabilidade:bem_baixar", args=[b.pk]),
                             {"data": "2026-05-01", "valor_venda": "0", "motivo": "Sucata"})
        b.refresh_from_db()
        self.assertEqual(b.status, "baixado")
        call_command("depreciar_bens", ate="2026-12", verbosity=0)

    def test_sem_permissao_nao_baixa(self):
        b = self.bem()
        u = User.objects.create_user("leitor", password="x")
        u.groups.add(Group.objects.get(name="Auditoria"))
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("contabilidade:bem_baixar", args=[b.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("contabilidade:depreciar")).status_code, 403)


class OrcamentoTest(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.adm = CentroCusto.objects.create(codigo="ADM", nome="Administrativo")
        cls.com = CentroCusto.objects.create(codigo="COM", nome="Comercial")
        cls.desp = PlanoConta.objects.get(codigo="2.02")
        cls.aluguel = PlanoConta.objects.get(codigo="2.02.01")
        cls.rec = PlanoConta.objects.get(codigo="1.01")
        cls.resp = User.objects.create_user("gestor", password="x")
        cls.ano = 2026

    def orc(self, controle="nenhum", **kw):
        return Orcamento.objects.create(nome="Anual", ano=self.ano, controle=controle, responsavel=self.resp, **kw)

    def linha(self, o, anual="12000", tipo="despesa", centro=None, cat=None, **kw):
        return LinhaOrcamento.objects.create(orcamento=o, tipo=tipo, centro_custo=centro, plano_conta=cat,
                                             valor_anual=D(anual), **kw)

    def pagar(self, valor, mes=3, cat=None, centro=None, **kw):
        return ContaPagar.objects.create(descricao="x", valor=D(valor), vencimento=datetime.date(self.ano, mes, 28),
                                         data_emissao=datetime.date(self.ano, mes, 5), plano_conta=cat,
                                         centro_custo=centro, **kw)

    def test_valor_anual_distribui_por_igual_sem_perder_centavo(self):
        ln = self.linha(self.orc(), anual="1000", centro=self.adm)
        self.assertEqual(ln.total, D("1000.00"))
        self.assertEqual(ln.m01, D("83.33"))
        self.assertEqual(ln.m12, D("83.37"))
        self.assertEqual(ln.ate_mes(3), D("249.99"))

    def test_meses_digitados_prevalecem(self):
        ln = self.linha(self.orc(), anual="0", centro=self.adm, m01=D("100"), m02=D("50"))
        self.assertEqual(ln.total, D("150"))

    def test_linha_exige_centro_ou_categoria_e_tipo_coerente(self):
        o = self.orc()
        with self.assertRaises(ValidationError):
            LinhaOrcamento(orcamento=o, tipo="despesa", valor_anual=D("1")).full_clean()
        with self.assertRaises(ValidationError):
            LinhaOrcamento(orcamento=o, tipo="despesa", plano_conta=self.rec, valor_anual=D("1")).full_clean()

    def test_realizado_por_centro_e_categoria_incluindo_filhas(self):
        o = self.orc()
        l1 = self.linha(o, centro=self.adm, cat=self.desp)
        self.pagar(300, mes=1, cat=self.aluguel, centro=self.adm)        # categoria filha casa com a pai
        self.pagar(200, mes=2, cat=self.aluguel, centro=self.adm)
        self.pagar(999, mes=2, cat=self.aluguel, centro=self.com)        # outro centro: fora
        self.pagar(500, mes=5, cat=self.aluguel, centro=self.adm)        # depois do mês de corte
        q = {x["nome"]: x for x in orc_s.quadro(o, 3)}
        linha = q[str(l1)]
        self.assertEqual(linha["realizado"], D("500"))
        self.assertEqual(linha["orcado"], D("3000"))
        self.assertEqual(linha["pct"], D("16.7"))
        self.assertFalse(linha["ruim"])
        self.assertEqual(q["Sem orçamento (despesas)"]["realizado"], D("999"))

    def test_linha_mais_especifica_vence(self):
        o = self.orc()
        geral = self.linha(o, centro=self.adm)
        especifica = self.linha(o, centro=self.adm, cat=self.aluguel)
        self.pagar(100, cat=self.aluguel, centro=self.adm)
        self.pagar(70, cat=self.desp, centro=self.adm)
        por_linha, _ = orc_s.realizado(o)
        self.assertEqual(sum(por_linha[especifica.pk]), D("100"))
        self.assertEqual(sum(por_linha[geral.pk]), D("70"))

    def test_competencia_define_o_mes_e_cancelado_fica_de_fora(self):
        o = self.orc()
        ln = self.linha(o, centro=self.adm)
        t = self.pagar(100, mes=1, centro=self.adm, competencia=datetime.date(self.ano, 4, 1))
        c = self.pagar(900, mes=2, centro=self.adm)
        c.cancelar()
        por_linha, _ = orc_s.realizado(o)
        self.assertEqual(por_linha[ln.pk][3], D("100"))
        self.assertEqual(sum(por_linha[ln.pk]), D("100"))

    def test_rateio_reparte_o_realizado(self):
        o = self.orc()
        a = self.linha(o, centro=self.adm)
        c = self.linha(o, centro=self.com)
        t = self.pagar(1000, mes=2)
        definir_rateio(t, [(self.adm, D("70")), (self.com, D("30"))])
        por_linha, _ = orc_s.realizado(o)
        self.assertEqual(sum(por_linha[a.pk]), D("700.00"))
        self.assertEqual(sum(por_linha[c.pk]), D("300.00"))

    def test_receita_orcada_abaixo_do_realizado_e_ruim(self):
        o = self.orc()
        self.linha(o, anual="12000", tipo="receita", cat=self.rec)
        ContaReceber.objects.create(descricao="v", valor=D("500"), vencimento=datetime.date(self.ano, 1, 20),
                                    data_emissao=datetime.date(self.ano, 1, 5), plano_conta=self.rec)
        q = orc_s.quadro(o, 1)[0]
        self.assertEqual((q["orcado"], q["realizado"]), (D("1000.00"), D("500.00")))
        self.assertTrue(q["ruim"])
        self.assertEqual(orc_s.totais([q])["receita"]["variacao"], D("-500.00"))

    def test_estouro_acumulado_do_ano(self):
        o = self.orc("alertar")
        self.linha(o, anual="12000", centro=self.adm)            # 1000/mês
        self.pagar(1500, mes=1, centro=self.adm)                 # estoura jan, mas aviso é só de novo título
        t = ContaPagar(descricao="y", valor=D("600"), vencimento=datetime.date(self.ano, 2, 28),
                       data_emissao=datetime.date(self.ano, 2, 5), centro_custo=self.adm)
        e = orc_s.estouro(t)
        self.assertEqual(e["orcado"], D("2000.00"))
        self.assertEqual(e["realizado"], D("2100"))
        self.assertEqual(e["excesso"], D("100"))
        t2 = ContaPagar(descricao="y", valor=D("100"), vencimento=datetime.date(self.ano, 2, 28),
                        data_emissao=datetime.date(self.ano, 2, 5), centro_custo=self.adm)
        self.assertIsNone(orc_s.estouro(t2))                      # 1600 < 2000 acumulado

    def test_sem_linha_ou_sem_controle_nao_ha_estouro(self):
        o = self.orc("nenhum")
        self.linha(o, centro=self.adm)
        t = ContaPagar(descricao="y", valor=D("99999"), vencimento=datetime.date(self.ano, 2, 28),
                       data_emissao=datetime.date(self.ano, 2, 5), centro_custo=self.adm)
        self.assertIsNone(orc_s.estouro(t))
        o.controle = "bloquear"
        o.save()
        t.centro_custo = self.com
        self.assertIsNone(orc_s.estouro(t))                       # nenhuma linha cobre o Comercial

    def test_alerta_notifica_o_responsavel(self):
        o = self.orc("alertar")
        self.linha(o, anual="1200", centro=self.adm)
        self.pagar(500, mes=1, centro=self.adm)
        self.assertEqual(Notificacao.objects.filter(usuario=self.resp, texto__contains="Orçamento estourado").count(), 1)
        self.pagar(10, mes=1, centro=self.adm)
        self.assertEqual(Notificacao.objects.filter(usuario=self.resp).count(), 2)

    def test_bloqueio_no_formulario(self):
        o = self.orc("bloquear")
        self.linha(o, anual="1200", centro=self.adm)
        dados = {"descricao": "Compra", "valor": "500.00", "data_emissao": f"{self.ano}-01-05",
                 "vencimento": f"{self.ano}-01-30", "juros": "0", "desconto": "0", "centro_custo": self.adm.pk, "parcelas": 1,
                 "intervalo_dias": 30}
        f = ContaPagarForm(dados)
        self.assertFalse(f.is_valid())
        self.assertIn("Estoura o orçamento", str(f.errors))
        dados["valor"] = "50.00"
        self.assertTrue(ContaPagarForm(dados).is_valid(), ContaPagarForm(dados).errors)

    def test_tela_do_relatorio_e_permissao(self):
        o = self.orc()
        self.linha(o, centro=self.adm)
        self.pagar(250, mes=1, centro=self.adm)
        self.client.force_login(self.cont)
        r = self.client.get(reverse("contabilidade:orcado_realizado", args=[o.pk]), {"ate": 3})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "250,00")
        self.assertEqual(self.client.get(reverse("contabilidade:orcamento_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("contabilidade:orcamento_create")).status_code, 200)
        u = User.objects.create_user("semperm", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("contabilidade:orcado_realizado", args=[o.pk])).status_code, 403)
