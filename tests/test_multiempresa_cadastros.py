from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.contabilidade import servicos
from apps.contabilidade.models import ContaContabil, Lancamento
from apps.core.models import Cliente, Empresa
from apps.crm.models import Oportunidade
from apps.estoque.models import Armazem
from apps.manutencao.models import Equipamento


class SeparacaoPorEmpresaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.a = Empresa.objects.create(razao_social="Alfa Ltda", cnpj="11.444.777/0001-61", matriz=True)
        cls.b = Empresa.objects.create(razao_social="Beta Ltda", cnpj="11.222.333/0001-81")
        cls.ua = User.objects.create_user("ua", password="x")
        cls.a.usuarios.add(cls.ua)
        cls.b.usuarios.add(User.objects.create_user("ub", password="x"))
        cls.cli = Cliente.objects.create(nome="Cliente", documento="11.444.777/0001-61")

    def test_armazem_codigo_repete_entre_empresas(self):
        a1 = Armazem.objects.create(empresa=self.a, codigo="01", nome="Almox A", padrao=True)
        b1 = Armazem.objects.create(empresa=self.b, codigo="01", nome="Almox B", padrao=True)
        a1.refresh_from_db()
        self.assertTrue(a1.padrao and b1.padrao)
        self.assertEqual(Armazem.principal(self.a), a1)
        self.assertEqual(Armazem.principal(self.b), b1)

    def test_empresa_preenchida_na_gravacao(self):
        op = Oportunidade.objects.create(cliente=self.cli, titulo="Venda", valor=100, estagio="proposta")
        self.assertIsNotNone(op.empresa)

    def test_lancamentos_por_empresa(self):
        caixa = ContaContabil.objects.get(codigo="1.1.1")
        capital = ContaContabil.objects.get(codigo="2.3.1")
        hoje = timezone.localdate()
        la = servicos.gravar(hoje, "A", caixa, capital, 100, manual=True, empresa=self.a)
        lb = servicos.gravar(hoje, "B", caixa, capital, 40, manual=True, empresa=self.b)
        self.assertEqual((la.empresa, lb.empresa), (self.a, self.b))
        self.assertEqual(la.estornar().empresa, self.a)
        sa = servicos.saldos(hoje, hoje.replace(day=1), self.a)
        sb = servicos.saldos(hoje, hoje.replace(day=1), self.b)
        self.assertNotEqual(str(sa), str(sb))

    def test_listas_isoladas_por_usuario(self):
        Equipamento.objects.create(empresa=self.a, codigo="EQ1", nome="Prensa A")
        Equipamento.objects.create(empresa=self.b, codigo="EQ2", nome="Prensa B")
        self.ua.user_permissions.add(Permission.objects.get(codename="view_equipamento"))
        self.client.force_login(self.ua)
        r = self.client.get(reverse("manutencao:equipamento_list"))
        self.assertContains(r, "Prensa A")
        self.assertNotContains(r, "Prensa B")
