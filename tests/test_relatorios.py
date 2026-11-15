import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cliente, Empresa
from apps.financeiro.models import ContaBancaria, ContaReceber, Movimentacao
from apps.relatorios import dashboards, filtros
from apps.vendas.models import PedidoVenda


def valor(blocos, rotulo):
    return next(b["texto"] for b in blocos if b["tipo"] == "kpi" and b["rotulo"] == rotulo)


@override_settings(SERVICEDESK_HORARIO="24x7")
class RelatoriosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.hoje = timezone.localdate()
        cls.a = Empresa.objects.create(razao_social="Alfa Ltda", cnpj="11.444.777/0001-61", matriz=True)
        cls.b = Empresa.objects.create(razao_social="Beta Ltda", cnpj="11.222.333/0001-81")
        cls.fin = User.objects.create_user("fin", password="x")
        cls.fin.groups.add(Group.objects.get(name="Financeiro"))
        cls.rh = User.objects.create_user("rh", password="x")
        cls.rh.groups.add(Group.objects.get(name="RH"))
        cls.comum = User.objects.create_user("comum", password="x")
        cls.comum.groups.add(Group.objects.get(name="Usuários"))
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.cliente = Cliente.objects.create(nome="Cliente Um", documento="11.444.777/0001-61")
        for emp, bruto in ((cls.a, "1000"), (cls.b, "250")):
            conta = ContaBancaria.objects.create(nome=f"Conta {emp.razao_social}", empresa=emp,
                                                 saldo_inicial=Decimal("100"), data_saldo_inicial=cls.hoje.replace(day=1))
            Movimentacao.objects.create(empresa=emp, conta_bancaria=conta, data=cls.hoje, tipo="entrada",
                                        valor=Decimal(bruto), descricao="Recebimento")
            Movimentacao.objects.create(empresa=emp, conta_bancaria=conta, data=cls.hoje, tipo="saida",
                                        valor=Decimal("100"), descricao="Despesa")
            ContaReceber.objects.create(empresa=emp, cliente=cls.cliente, descricao="Venda", valor=Decimal("500"),
                                        vencimento=cls.hoje - datetime.timedelta(days=40))
            p = PedidoVenda.objects.create(empresa=emp, cliente=cls.cliente, data_pedido=cls.hoje)
            PedidoVenda.objects.filter(pk=p.pk).update(status="faturado", valor_total=Decimal(bruto))

    def filtro(self, user, **get):
        class R:
            pass
        r = R()
        r.user, r.GET = user, {k: str(v) for k, v in get.items()}
        return filtros.do_request(r)

    def test_periodos_e_comparacao(self):
        f = self.filtro(self.admin, periodo="mes")
        self.assertEqual((f.ini, f.fim), (self.hoje.replace(day=1), self.hoje))
        self.assertEqual(f.ant_fim, f.ini - datetime.timedelta(days=1))
        self.assertEqual((f.fim - f.ini).days + 1, (f.ant_fim - f.ant_ini).days + 1)
        f = self.filtro(self.admin, periodo="12m")
        self.assertEqual(len(f.meses), 12)
        f = self.filtro(self.admin, periodo="custom", de="2026-01-10", ate="2026-01-05")   # invertido: cai no padrão
        self.assertEqual(f.periodo, "12m")
        self.assertEqual(filtros.variacao(150, 100), 50.0)
        self.assertIsNone(filtros.variacao(10, 0))

    def test_filtro_por_empresa_recorta_os_numeros(self):
        todas = dashboards.financeiro(self.filtro(self.admin, periodo="mes"))
        so_a = dashboards.financeiro(self.filtro(self.admin, periodo="mes", empresa=self.a.pk))
        so_b = dashboards.financeiro(self.filtro(self.admin, periodo="mes", empresa=self.b.pk))
        self.assertEqual(valor(todas, "Entradas (caixa)"), "R$ 1.250,00")
        self.assertEqual(valor(so_a, "Entradas (caixa)"), "R$ 1.000,00")
        self.assertEqual(valor(so_b, "Entradas (caixa)"), "R$ 250,00")
        self.assertEqual(valor(so_a, "Saldo em contas"), "R$ 1.000,00")      # 100 inicial + 1000 − 100
        self.assertEqual(valor(so_b, "Inadimplência"), "100,0%")
        fat = dashboards.executivo(self.filtro(self.admin, periodo="mes", empresa=self.b.pk))
        self.assertEqual(valor(fat, "Faturamento"), "R$ 250,00")

    def test_usuario_sem_acesso_a_empresa_nao_ve_os_numeros_dela(self):
        self.b.usuarios.add(self.admin)               # a Beta passa a ser restrita a outra pessoa
        f = self.filtro(self.fin, periodo="mes", empresa=self.b.pk)
        self.assertIsNone(f.empresa)                   # empresa inacessível volta para "todas que eu acesso"
        blocos = dashboards.financeiro(f)
        self.assertEqual(valor(blocos, "Entradas (caixa)"), "R$ 1.000,00")
        self.assertEqual(valor(dashboards.executivo(f), "Faturamento"), "R$ 1.000,00")   # Financeiro lê pedidos de venda

    def test_views_exigem_permissao(self):
        self.client.force_login(self.comum)
        self.assertEqual(self.client.get(reverse("relatorios:executivo")).status_code, 200)
        for nome in ("financeiro", "comercial", "suprimentos", "servicedesk"):
            self.assertEqual(self.client.get(reverse(f"relatorios:{nome}")).status_code, 403, nome)
        self.client.force_login(self.fin)
        self.assertEqual(self.client.get(reverse("relatorios:financeiro")).status_code, 200)
        # o painel executivo só traz o que o perfil vê
        self.client.force_login(self.comum)
        r = self.client.get(reverse("relatorios:executivo"))
        self.assertEqual([b for b in r.context["blocos"] if b["tipo"] != "aviso"], [])
        self.client.force_login(self.rh)       # equipe de atendimento: só o painel do Service Desk, da própria área
        rotulos = {b.get("rotulo") for b in self.client.get(reverse("relatorios:executivo")).context["blocos"]}
        self.assertEqual(rotulos - {None}, {"Chamados em andamento"})
        self.client.force_login(self.fin)
        rotulos = {b.get("rotulo") for b in self.client.get(reverse("relatorios:executivo")).context["blocos"]}
        self.assertTrue({"Saldo em contas", "Faturamento"} <= rotulos)
        self.assertNotIn("Pipeline em aberto", rotulos)

    def test_todos_os_paineis_abrem_para_o_administrador(self):
        self.client.force_login(self.admin)
        for nome in ("executivo", "financeiro", "comercial", "suprimentos", "operacao", "servicedesk"):
            for q in ("", "?periodo=mes", f"?periodo=custom&de={self.hoje:%Y-%m-%d}&ate={self.hoje:%Y-%m-%d}",
                      f"?empresa={self.a.pk}&periodo=ano", "?empresa=abc&periodo=xx"):
                r = self.client.get(reverse(f"relatorios:{nome}") + q)
                self.assertEqual(r.status_code, 200, f"{nome} {q}")

    def test_exportacao_csv_protegida_contra_formula(self):
        ContaReceber.objects.create(empresa=self.a, cliente=Cliente.objects.create(nome="=HYPERLINK(1)", documento="52.998.224/0001-62"),
                                    descricao="x", valor=Decimal("900"), vencimento=self.hoje)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("relatorios:financeiro") + "?periodo=mes&csv=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
        corpo = r.content.decode("utf-8-sig")
        self.assertIn("Cliente;Em aberto", corpo)
        self.assertIn("'=HYPERLINK(1)", corpo)
        self.assertEqual(self.client.get(reverse("relatorios:financeiro") + "?csv=99").status_code, 200)

    def test_menu_mostra_so_o_que_o_perfil_acessa(self):
        self.client.force_login(self.fin)
        html = self.client.get(reverse("relatorios:executivo")).content.decode()
        self.assertIn("relatorios/financeiro/", html)
        self.client.force_login(self.comum)
        html = self.client.get(reverse("relatorios:executivo")).content.decode()
        self.assertNotIn("relatorios/financeiro/", html)
        self.assertNotIn("relatorios/service-desk/", html)
