"""Contratos: índices e reajuste, medições, garantias e paralisações."""
import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cliente, Empresa, Fornecedor
from apps.financeiro.models import ContaPagar, ContaReceber
from apps.juridico.models import Caucao, Contrato, Medicao, Paralisacao, ValorIndice, processar_vencimentos, somar_meses

HOJE = timezone.localdate()
D = Decimal


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.empresa = Empresa.objects.create(razao_social="Empresa Teste", cnpj="11222333000181")
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.forn = Fornecedor.objects.create(nome="Fornecedor X")
        cls.cli = Cliente.objects.create(nome="Cliente Y")

    def contrato(self, tipo="fornecedor", **kw):
        dados = dict(titulo="Manutenção", tipo=tipo, inicio_vigencia=HOJE.replace(day=1) - datetime.timedelta(days=200),
                     fim_vigencia=HOJE + datetime.timedelta(days=165), valor_mensal=D("1000"), valor_total=D("12000"),
                     indice_reajuste="ipca", empresa=self.empresa)
        dados.update(kw)
        if tipo == "fornecedor":
            dados.setdefault("fornecedor", self.forn)
        elif tipo == "cliente":
            dados.setdefault("cliente", self.cli)
        c = Contrato.objects.create(**dados)
        c.status = "vigente"
        c.save()
        return c

    def medicao(self, c, **kw):
        dados = dict(contrato=c, periodo_inicio=HOJE.replace(day=1) - datetime.timedelta(days=60),
                     periodo_fim=HOJE.replace(day=1) - datetime.timedelta(days=31), valor=D("1000"))
        dados.update(kw)
        return Medicao.objects.create(**dados)


class IndiceEReajuste(Base):
    def cadastrar(self, indice, meses, var):
        ref = somar_meses(HOJE.replace(day=1), -1)
        for i in range(meses):
            ValorIndice.objects.create(indice=indice, competencia=somar_meses(ref, -i), variacao=D(var))

    def test_acumulado_compoe_meses(self):
        self.cadastrar("ipca", 12, "1.0")
        self.assertEqual(ValorIndice.acumulado("ipca", somar_meses(HOJE.replace(day=1), -1)), D("12.68"))

    def test_competencia_vira_dia_1_e_unica(self):
        v = ValorIndice.objects.create(indice="igpm", competencia=datetime.date(2026, 3, 17), variacao=D("0.5"))
        self.assertEqual(v.competencia.day, 1)
        with self.assertRaises(Exception):
            ValorIndice.objects.create(indice="igpm", competencia=datetime.date(2026, 3, 5), variacao=D("0.1"))

    def test_mes_faltando_lista_meses(self):
        self.cadastrar("ipca", 10, "0.5")
        with self.assertRaises(ValidationError) as e:
            ValorIndice.acumulado("ipca", somar_meses(HOJE.replace(day=1), -1))
        self.assertIn("Faltam", e.exception.messages[0])

    def test_sugestao_e_reajuste(self):
        self.cadastrar("ipca", 12, "0.5")
        c = self.contrato()
        pct = c.sugestao_reajuste()
        self.assertEqual(pct, D("6.17"))
        c.reajustar(self.admin, pct)
        c.refresh_from_db()
        self.assertEqual(c.valor_mensal, D("1061.70"))
        self.assertEqual(c.valor_total, D("12740.40"))
        ad = c.aditivos.get(tipo="reajuste")
        self.assertEqual(ad.valor_anterior, D("1000.00"))
        self.assertTrue(c.eventos.filter(tipo="reajuste").exists())

    def test_sugestao_sem_indice_oficial(self):
        c = self.contrato(indice_reajuste="fixo")
        with self.assertRaises(ValidationError):
            c.sugestao_reajuste()

    def test_reajuste_exige_vigente(self):
        c = self.contrato()
        c.status = "encerrado"
        c.save()
        with self.assertRaises(ValidationError):
            c.reajustar(self.admin, D("5"))

    def test_view_reajustar(self):
        c = self.contrato()
        self.client.force_login(self.admin)
        r = self.client.post(reverse("juridico:reajustar", args=[c.pk]), {"percentual": "4.50"})
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.valor_mensal, D("1045.00"))
        self.assertEqual(self.client.get(reverse("juridico:reajustar", args=[c.pk])).status_code, 200)


class Medicoes(Base):
    def test_numeracao_sequencial_por_contrato(self):
        c1, c2 = self.contrato(), self.contrato()
        self.assertEqual([self.medicao(c1).numero, self.medicao(c1).numero, self.medicao(c2).numero], [1, 2, 1])

    def test_liquido(self):
        m = self.medicao(self.contrato(), valor=D("1000"), multas=D("100"), bonificacoes=D("30"))
        self.assertEqual(m.valor_liquido, D("930"))

    def test_aprovar_e_faturar_fornecedor(self):
        c = self.contrato()
        m = self.medicao(c, multas=D("50"), vencimento=HOJE + datetime.timedelta(days=10))
        m.aprovar(self.admin)
        t = m.gerar_titulo(self.admin)
        self.assertIsInstance(t, ContaPagar)
        self.assertEqual((t.valor, t.fornecedor, t.documento, t.empresa), (D("950"), self.forn, f"{c.numero}/M1", self.empresa))
        m.refresh_from_db()
        self.assertEqual(m.status, "faturada")
        with self.assertRaises(ValidationError):
            m.gerar_titulo()

    def test_faturar_cliente_gera_conta_receber(self):
        m = self.medicao(self.contrato("cliente"))
        m.aprovar(self.admin)
        self.assertIsInstance(m.gerar_titulo(), ContaReceber)

    def test_outra_parte_nao_gera_titulo(self):
        c = self.contrato("outro", outra_parte="Parceiro")
        m = self.medicao(c)
        m.aprovar(self.admin)
        with self.assertRaises(ValidationError):
            m.gerar_titulo()

    def test_periodo_fora_da_vigencia(self):
        c = self.contrato()
        m = self.medicao(c, periodo_inicio=c.inicio_vigencia - datetime.timedelta(days=40),
                         periodo_fim=c.inicio_vigencia - datetime.timedelta(days=10))
        with self.assertRaises(ValidationError):
            m.aprovar(self.admin)

    def test_limite_do_valor_total(self):
        c = self.contrato(valor_total=D("1500"))
        self.medicao(c).aprovar(self.admin)
        m2 = self.medicao(c, valor=D("600"))
        with self.assertRaises(ValidationError) as e:
            m2.aprovar(self.admin)
        self.assertIn("estoura", e.exception.messages[0])
        self.medicao(c, valor=D("500")).aprovar(self.admin)  # 1500 fecha o contrato

    def test_contrato_nao_vigente(self):
        c = self.contrato()
        m = self.medicao(c)
        c.status = "encerrado"
        c.save()
        with self.assertRaises(ValidationError):
            m.aprovar(self.admin)

    def test_multa_nao_pode_superar_valor(self):
        m = Medicao(contrato=self.contrato(), periodo_inicio=HOJE, periodo_fim=HOJE, valor=D("100"), multas=D("200"))
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_aprovada_nao_edita(self):
        m = self.medicao(self.contrato())
        m.aprovar(self.admin)
        m.valor = D("5000")
        with self.assertRaises(ValidationError):
            m.full_clean()

    def test_cancelar_cancela_titulo_mas_nao_se_pago(self):
        m = self.medicao(self.contrato())
        m.aprovar(self.admin)
        t = m.gerar_titulo()
        m.cancelar(self.admin)
        t.refresh_from_db()
        self.assertEqual((m.status, t.status), ("cancelada", "cancelado"))

        m2 = self.medicao(self.contrato())
        m2.aprovar(self.admin)
        t2 = m2.gerar_titulo()
        ContaPagar.objects.filter(pk=t2.pk).update(valor_pago=D("10"))
        m2 = Medicao.objects.get(pk=m2.pk)
        with self.assertRaises(ValidationError):
            m2.cancelar()
        m2.refresh_from_db()
        self.assertEqual(m2.status, "faturada")

    def test_cancelada_libera_limite(self):
        c = self.contrato(valor_total=D("1000"))
        m = self.medicao(c)
        m.aprovar(self.admin)
        m.cancelar()
        self.medicao(c).aprovar(self.admin)

    def test_views_fluxo(self):
        c = self.contrato()
        m = self.medicao(c)
        self.client.force_login(self.admin)
        for nome in ("medicao_aprovar", "medicao_faturar"):
            url = reverse(f"juridico:{nome}", args=[m.pk])
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.post(url).status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.status, "faturada")
        self.assertEqual(self.client.get(reverse("juridico:medicao_detail", args=[m.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("juridico:medicao_list")).status_code, 200)

    def test_crud_cria_medicao(self):
        c = self.contrato()
        self.client.force_login(self.admin)
        r = self.client.post(reverse("juridico:medicao_create"), {
            "contrato": c.pk, "periodo_inicio": (HOJE - datetime.timedelta(days=40)).isoformat(),
            "periodo_fim": (HOJE - datetime.timedelta(days=10)).isoformat(), "valor": "800", "multas": "0", "bonificacoes": "0"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.assertEqual(c.medicoes.count(), 1)


class Garantias(Base):
    def test_liberar_e_executar(self):
        c = self.contrato()
        g = Caucao.objects.create(contrato=c, tipo="seguro", valor=D("5000"), validade=HOJE + datetime.timedelta(days=100))
        g.liberar(self.admin)
        self.assertEqual(g.status, "devolvida")
        with self.assertRaises(ValidationError):
            g.liberar(self.admin, executar=True)
        g2 = Caucao.objects.create(contrato=c, tipo="dinheiro", valor=D("100"))
        g2.liberar(self.admin, executar=True)
        self.assertEqual(g2.status, "executada")

    def test_validade_antes_da_emissao(self):
        g = Caucao(contrato=self.contrato(), valor=D("1"), emissao=HOJE, validade=HOJE - datetime.timedelta(days=1))
        with self.assertRaises(ValidationError):
            g.full_clean()

    def test_alerta_de_vencimento_uma_vez(self):
        from apps.tarefas.models import Atividade

        c = self.contrato()
        g = Caucao.objects.create(contrato=c, tipo="seguro", valor=D("5000"), validade=HOJE + datetime.timedelta(days=20))
        Caucao.objects.create(contrato=c, tipo="seguro", valor=D("1"), validade=HOJE + datetime.timedelta(days=200))
        antes = Atividade.objects.count()
        processar_vencimentos()
        self.assertEqual(Atividade.objects.filter(titulo__startswith="Garantia a vencer").count(), 1)
        processar_vencimentos()
        self.assertEqual(Atividade.objects.count(), antes + 1)
        g.refresh_from_db()
        self.assertEqual(g.ultimo_alerta, g.validade)

    def test_view_devolver(self):
        g = Caucao.objects.create(contrato=self.contrato(), valor=D("10"))
        self.client.force_login(self.admin)
        self.client.post(reverse("juridico:caucao_devolver", args=[g.pk]))
        g.refresh_from_db()
        self.assertEqual(g.status, "devolvida")


class Paralisacoes(Base):
    def test_retomar_estende_prazo(self):
        c = self.contrato()
        fim = c.fim_vigencia
        p = Paralisacao.objects.create(contrato=c, inicio=HOJE - datetime.timedelta(days=10), motivo="Obra embargada")
        p.retomar(self.admin, HOJE - datetime.timedelta(days=1))
        c.refresh_from_db()
        self.assertEqual(p.dias, 10)
        self.assertEqual(c.fim_vigencia, fim + datetime.timedelta(days=10))
        ad = c.aditivos.get(tipo="prazo")
        self.assertEqual((ad.fim_anterior, ad.nova_data_fim), (fim, c.fim_vigencia))

    def test_sem_extensao(self):
        c = self.contrato()
        fim = c.fim_vigencia
        p = Paralisacao.objects.create(contrato=c, inicio=HOJE - datetime.timedelta(days=5), motivo="x", estende_prazo=False)
        p.retomar(self.admin, HOJE)
        c.refresh_from_db()
        self.assertEqual(c.fim_vigencia, fim)
        self.assertFalse(c.aditivos.filter(tipo="prazo").exists())

    def test_so_uma_aberta_e_retomada_valida(self):
        c = self.contrato()
        p = Paralisacao.objects.create(contrato=c, inicio=HOJE, motivo="x")
        with self.assertRaises(ValidationError):
            Paralisacao(contrato=c, inicio=HOJE, motivo="y").clean()
        with self.assertRaises(ValidationError):
            p.retomar(self.admin, HOJE - datetime.timedelta(days=1))
        p.retomar(self.admin, HOJE)
        with self.assertRaises(ValidationError):
            p.retomar(self.admin, HOJE)

    def test_view_retomar_e_detalhe_do_contrato(self):
        c = self.contrato()
        p = Paralisacao.objects.create(contrato=c, inicio=HOJE - datetime.timedelta(days=3), motivo="x")
        self.client.force_login(self.admin)
        url = reverse("juridico:paralisacao_retomar", args=[p.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"fim": HOJE.isoformat()})
        p.refresh_from_db()
        self.assertFalse(p.ativa)
        self.medicao(c)
        self.assertEqual(self.client.get(reverse("juridico:contrato_detail", args=[c.pk])).status_code, 200)
