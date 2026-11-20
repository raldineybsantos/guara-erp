import datetime
import json
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.contabilidade import contratos, servicos
from apps.contabilidade.models import ApropriacaoContrato, ContaContabil, ContratoContabil
from apps.core.consulta import consultar_cnpj
from apps.core.models import Empresa, Fornecedor
from apps.juridico.models import Contrato

BRASILAPI = {"razao_social": "EMPRESA TESTE LTDA", "nome_fantasia": "TESTE", "email": "A@B.COM",
             "ddd_telefone_1": "1133334444", "cep": "01001000", "descricao_tipo_de_logradouro": "RUA",
             "logradouro": "DAS FLORES", "numero": "10", "complemento": "", "bairro": "CENTRO",
             "municipio": "SAO PAULO", "uf": "SP", "codigo_municipio_ibge": 3550308, "cnae_fiscal": 6201501,
             "descricao_situacao_cadastral": "ATIVA"}


class _Resp:
    def __init__(self, dados):
        self.dados = dados

    def read(self):
        return json.dumps(self.dados).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConsultaCnpjTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("u", password="x")
        self.client.force_login(self.user)

    def test_normaliza_dados(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(BRASILAPI)):
            d = consultar_cnpj("11.444.777/0001-61")
        self.assertEqual(d["razao_social"], "EMPRESA TESTE LTDA")
        self.assertEqual(d["logradouro"], "RUA DAS FLORES")
        self.assertEqual(d["codigo_municipio"], "3550308")
        self.assertEqual(d["email"], "a@b.com")
        self.assertFalse(d["inativa"])

    def test_situacao_inativa(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_Resp({**BRASILAPI, "descricao_situacao_cadastral": "BAIXADA"})):
            self.assertTrue(consultar_cnpj("11444777000161")["inativa"])

    def test_cnpj_invalido_nao_consulta(self):
        with mock.patch("urllib.request.urlopen") as m:
            with self.assertRaises(ValidationError):
                consultar_cnpj("11.111.111/1111-11")
        m.assert_not_called()

    def test_sem_internet(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
            with self.assertRaises(ValidationError):
                consultar_cnpj("11444777000161")

    def test_view_json_e_login(self):
        url = reverse("core:consulta_cnpj")
        with mock.patch("urllib.request.urlopen", return_value=_Resp(BRASILAPI)):
            r = self.client.get(url, {"cnpj": "11444777000161"})
        self.assertEqual(r.json()["cidade"], "SAO PAULO")
        r = self.client.get(url, {"cnpj": "123"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("erro", r.json())
        self.client.logout()
        self.assertEqual(self.client.get(url, {"cnpj": "11444777000161"}).status_code, 302)


class ContratoContabilTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.emp = Empresa.padrao()
        cls.desp = ContaContabil.objects.filter(natureza="despesa", sintetica=False).first()
        cls.pas = ContaContabil.objects.filter(natureza="passivo", sintetica=False).first()
        cls.rec = ContaContabil.objects.filter(natureza="receita", sintetica=False).first()
        cls.atv = ContaContabil.objects.filter(natureza="ativo", sintetica=False).first()
        cls.forn = Fornecedor.objects.create(nome="Locador", prazo_pagamento_dias=30)
        cls.ct = Contrato.objects.create(titulo="Aluguel", tipo="fornecedor", categoria="locacao", fornecedor=cls.forn,
                                         valor_mensal=D("1000"), inicio_vigencia=datetime.date(2026, 1, 1),
                                         fim_vigencia=datetime.date(2026, 6, 30))

    def cc(self, **kw):
        d = dict(contrato=self.ct, natureza="despesa", conta_resultado=self.desp, conta_balanco=self.pas,
                 valor_mensal=D("1000"), inicio_apropriacao=datetime.date(2026, 1, 15), empresa=self.emp)
        d.update(kw)
        return ContratoContabil.objects.create(**d)

    def test_apropria_ate_o_mes_e_e_idempotente(self):
        cc = self.cc()
        self.assertEqual(contratos.apropriar_ate(datetime.date(2026, 3, 10)), 3)
        self.assertEqual(contratos.apropriar_ate(datetime.date(2026, 3, 10)), 0)
        self.assertEqual(cc.apropriacoes.count(), 3)
        self.assertEqual(cc.total_apropriado, D("3000"))

    def test_respeita_fim_da_vigencia(self):
        self.cc()
        self.assertEqual(contratos.apropriar_ate(datetime.date(2026, 12, 1)), 6)

    def test_receita_inverte_partidas(self):
        cli_ct = Contrato.objects.create(titulo="Venda recorrente", tipo="outro", valor_mensal=D("500"),
                                         inicio_vigencia=datetime.date(2026, 1, 1))
        cc = self.cc(contrato=cli_ct, natureza="receita", conta_resultado=self.rec, conta_balanco=self.atv,
                     valor_mensal=D("500"))
        contratos.apropriar_ate(datetime.date(2026, 1, 1))
        from apps.contabilidade.models import Lancamento
        l = Lancamento.objects.get(evento="apropriacao_contrato")
        tipos = {x.conta_id: x.tipo for x in l.partidas.all()}
        self.assertEqual(tipos[self.atv.pk], "D")
        self.assertEqual(tipos[self.rec.pk], "C")

    def test_estorno_so_do_mais_recente(self):
        cc = self.cc()
        contratos.apropriar_ate(datetime.date(2026, 2, 1))
        with self.assertRaises(ValidationError):
            contratos.estornar_competencia(datetime.date(2026, 1, 1))
        self.assertEqual(contratos.estornar_competencia(datetime.date(2026, 2, 1)), 1)
        self.assertEqual(cc.apropriacoes.count(), 1)

    def test_inativo_nao_apropria(self):
        self.cc(ativo=False)
        self.assertEqual(contratos.apropriar_ate(datetime.date(2026, 3, 1)), 0)

    def test_validacoes(self):
        base = dict(contrato=self.ct, natureza="despesa", conta_resultado=self.desp, conta_balanco=self.pas,
                    valor_mensal=D("1000"), inicio_apropriacao=datetime.date(2026, 1, 1), empresa=self.emp)
        with self.assertRaises(ValidationError):
            ContratoContabil(**{**base, "conta_resultado": self.rec}).full_clean()
        with self.assertRaises(ValidationError):
            ContratoContabil(**{**base, "valor_mensal": D("0")}).full_clean()
        ContratoContabil(**base).full_clean()

    def test_tela_apropriar(self):
        self.cc()
        u = User.objects.create_user("c", password="x")
        u.groups.add(Group.objects.get(name="Contabilidade"))
        self.client.force_login(u)
        r = self.client.post(reverse("contabilidade:apropriar_contratos"),
                             {"ano": 2026, "mes": 2, "acao": "apropriar"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ApropriacaoContrato.objects.count(), 2)
        self.assertEqual(self.client.get(reverse("contabilidade:contratocontabil_list")).status_code, 200)


class MenuProdutosTest(TestCase):
    def test_produtos_no_menu_cadastros(self):
        u = User.objects.create_superuser("adm", "a@b.com", "x")
        self.client.force_login(u)
        html = self.client.get("/").content.decode()
        self.assertIn(reverse("fiscal:produto_list"), html)


class AjustesUsabilidadeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.adm = User.objects.create_superuser("adm2", "a@b.com", "x")

    def setUp(self):
        self.client.force_login(self.adm)

    def test_primeiros_passos(self):
        r = self.client.get(reverse("primeiros_passos"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Alçada de compras", r.content.decode())
        self.assertIn("passo(s) da configuração inicial", self.client.get("/").content.decode())

    def test_moeda_brasileira(self):
        from apps.core.formatacao import moeda
        self.assertEqual(moeda(D("1234.5")), "R$ 1.234,50")
        self.assertEqual(moeda(None), "")

    def test_botoes_por_status(self):
        from apps.core.models import Fornecedor
        from apps.compras.models import PedidoCompra
        f = Fornecedor.objects.create(nome="F", prazo_pagamento_dias=30)
        p = PedidoCompra.objects.create(fornecedor=f)
        html = self.client.get(p.get_absolute_url()).content.decode()
        self.assertIn(reverse("compras:enviar_pedido", args=[p.pk]), html)
        self.assertNotIn(reverse("compras:receber_pedido", args=[p.pk]), html)
        PedidoCompra.objects.filter(pk=p.pk).update(status="em_aprovacao")
        html = self.client.get(p.get_absolute_url()).content.decode()
        self.assertNotIn(reverse("compras:enviar_pedido", args=[p.pk]), html)

    def test_preco_item(self):
        from apps.fiscal.models import Produto
        pr = Produto.objects.create(codigo="X1", descricao="Item", unidade="UN", preco=D("28.90"))
        r = self.client.get(reverse("vendas:preco_item"), {"produto": pr.pk})
        self.assertEqual(r.json()["preco"], "28.90")
        self.assertEqual(self.client.get(reverse("vendas:preco_item"), {"produto": 999999}).status_code, 404)
