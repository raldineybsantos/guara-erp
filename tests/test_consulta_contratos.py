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


class ColaboradorMatriculaCepTest(TestCase):
    def setUp(self):
        from apps.core.models import Empresa
        self.emp = Empresa.objects.create(razao_social="E", cnpj="11222333000181")

    def _c(self, nome, cpf, **kw):
        from apps.core.models import Colaborador
        return Colaborador.objects.create(empresa=self.emp, nome=nome, cpf=cpf, **kw)

    def test_matricula_automatica_sequencial(self):
        a = self._c("A", "52998224725")
        b = self._c("B", "11144477735")
        self.assertEqual((a.matricula, b.matricula), ("000001", "000002"))

    def test_matricula_manual_preservada_e_ignora_nao_numerica(self):
        self._c("A", "52998224725", matricula="F-10")
        b = self._c("B", "11144477735")
        self.assertEqual(b.matricula, "000001")

    def test_cpf_gravado_so_com_digitos(self):
        a = self._c("A", "529.982.247-25")
        a.refresh_from_db()
        self.assertEqual(a.cpf, "52998224725")
        self.assertEqual(a.cpf_formatado, "529.982.247-25")

    def test_consultar_cep_valida_tamanho(self):
        from django.core.exceptions import ValidationError
        from apps.core.consulta import consultar_cep
        with self.assertRaises(ValidationError):
            consultar_cep("123")

    def test_consultar_cep_normaliza(self):
        from unittest import mock
        import io, json
        from apps.core import consulta
        resp = io.BytesIO(json.dumps({"street": "Rua A", "neighborhood": "Centro", "city": "São Paulo", "state": "sp"}).encode())
        with mock.patch.object(consulta.urllib.request, "urlopen", return_value=resp):
            r = consulta.consultar_cep("01001000")
        self.assertEqual(r, {"cep": "01001-000", "logradouro": "Rua A", "bairro": "Centro", "cidade": "São Paulo", "uf": "SP"})


class AcoesPorStatusTest(TestCase):
    """Todo Crud com acao_status deve apontar para ações e situações que existem."""

    def _cruds(self):
        import importlib, inspect
        from django.apps import apps
        from apps.core.crud import Crud
        for cfg in apps.get_app_configs():
            if not cfg.name.startswith("apps."):
                continue
            try:
                m = importlib.import_module(cfg.name + ".telas")
            except ImportError:
                continue
            for _n, c in inspect.getmembers(m, inspect.isclass):
                if issubclass(c, Crud) and c is not Crud and getattr(c, "model", None) and c.__module__ == m.__name__:
                    yield c

    def test_acao_status_consistente(self):
        total = 0
        for c in self._cruds():
            if not c.acao_status:
                continue
            nomes = {a[0] for a in c.actions}
            validos = {v for v, _ in c.model._meta.get_field("status").choices}
            for acao, situacoes in c.acao_status.items():
                total += 1
                self.assertIn(acao, nomes, f"{c.__name__}: ação inexistente {acao}")
                self.assertTrue(set(situacoes) <= validos, f"{c.__name__}.{acao}: situação inválida")
        self.assertGreater(total, 90)

    def test_toda_acao_de_modelo_com_status_tem_restricao(self):
        livres = []
        for c in self._cruds():
            if not c.actions or not any(f.name == "status" for f in c.model._meta.fields):
                continue
            for a in c.actions:
                if a[0] not in c.acao_status:
                    livres.append(f"{c.__name__}:{a[0]}")
        # ações que valem em qualquer situação (consulta, arquivo, PDF…)
        permitidas = {"compras:pdf_pedido", "financeiro:guia_darf", "rh:esocial_baixar", "esocial_baixar", "uniformes:entrega_termo"}
        livres = [x for x in livres if x.split(":", 1)[1] not in permitidas and not x.endswith(tuple(permitidas))]
        self.assertEqual(livres, [])
