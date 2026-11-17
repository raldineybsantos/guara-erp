"""Fechamento dos módulos parciais: projetos (compras/vendas), CRM (metas, campanhas), SST (CIPA, ambiental),
produção (roteiro) e qualidade (plano de inspeção)."""
import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.compras.models import ItemPedido, PedidoCompra
from apps.core.models import Cliente, Colaborador, Fornecedor
from apps.crm.models import Campanha, MetaVenda, Oportunidade
from apps.estoque import models as est
from apps.estoque.models import Armazem
from apps.financeiro.models import CentroCusto, ContaReceber
from apps.fiscal.models import Produto
from apps.producao.models import EstruturaProduto, OperacaoRoteiro, OrdemProducao
from apps.projetos.models import Projeto
from apps.qualidade.models import CaracteristicaInspecao, Inspecao, PlanoInspecao
from apps.sst.models import LicencaAmbiental, MembroCIPA, ReuniaoCIPA, ResiduoGerado, alertar_vencimentos
from apps.tarefas.models import Atividade
from apps.vendas.models import ItemPedidoVenda, PedidoVenda

HOJE = timezone.localdate()


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.vend = User.objects.create_user("vend", password="x")
        cls.vend.groups.add(Group.objects.get(name="Vendas"))
        cls.cli = Cliente.objects.create(nome="Cliente Y")
        cls.forn = Fornecedor.objects.create(nome="Fornecedor X")
        cls.prod = Produto.objects.create(codigo="P1", descricao="Peça", preco=100)
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.cc = CentroCusto.objects.create(nome="Obra A", codigo="OBRA-A")


class ProjetoComprasVendas(Base):
    def projeto(self):
        return Projeto.objects.create(nome="Obra A", gerente=self.admin, centro_custo=self.cc, orcamento=D("10000"))

    def test_compra_herda_centro_de_custo_do_projeto(self):
        pj = self.projeto()
        pc = PedidoCompra.objects.create(fornecedor=self.forn, projeto=pj)
        self.assertEqual(pc.centro_custo_id, self.cc.pk)

    def test_centro_de_custo_digitado_prevalece(self):
        outro = CentroCusto.objects.create(nome="Outro", codigo="OUT")
        pc = PedidoCompra.objects.create(fornecedor=self.forn, projeto=self.projeto(), centro_custo=outro)
        self.assertEqual(pc.centro_custo_id, outro.pk)

    def test_compras_comprometidas_so_contam_aprovadas(self):
        pj = self.projeto()
        for st, qtd in (("rascunho", 1), ("aprovado", 2), ("recebido", 3), ("cancelado", 4)):
            pc = PedidoCompra.objects.create(fornecedor=self.forn, projeto=pj)
            ItemPedido.objects.create(pedido=pc, produto=self.prod, descricao="x", quantidade=qtd, valor_unitario=D("10"))
            pc.recalcular()
            PedidoCompra.objects.filter(pk=pc.pk).update(status=st)
        self.assertEqual(pj.compras_comprometidas(), D("50.00"))

    def test_venda_faturada_gera_receita_e_titulo_no_centro_do_projeto(self):
        est.entrada(self.prod, 100, "40.00", self.a1)
        pj = self.projeto()
        p = PedidoVenda.objects.create(cliente=self.cli, vendedor=self.vend, projeto=pj)
        ItemPedidoVenda.objects.create(pedido=p, produto=self.prod, quantidade=5)
        p.recalcular()
        self.assertTrue(p.confirmar())
        p.faturar()
        self.assertEqual(pj.vendas_faturadas(), D("500.00"))
        self.assertTrue(ContaReceber.objects.filter(centro_custo=self.cc).exists())
        self.assertEqual(pj.vendas_em_carteira(), D("0"))

    def test_resultado_do_projeto(self):
        pj = self.projeto()
        p = PedidoVenda.objects.create(cliente=self.cli, vendedor=self.vend, projeto=pj)
        ItemPedidoVenda.objects.create(pedido=p, produto=self.prod, quantidade=10)
        p.recalcular()
        PedidoVenda.objects.filter(pk=p.pk).update(status="faturado")
        self.assertEqual(pj.resultado(), D("1000.00"))
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(pj.get_absolute_url()).status_code, 200)


class CRMMetasCampanhas(Base):
    def test_meta_normaliza_mes_e_compara_com_faturado(self):
        m = MetaVenda.objects.create(vendedor=self.vend, competencia=HOJE.replace(day=17), valor_meta=D("1000"))
        self.assertEqual(m.competencia.day, 1)
        p = PedidoVenda.objects.create(cliente=self.cli, vendedor=self.vend)
        ItemPedidoVenda.objects.create(pedido=p, produto=self.prod, quantidade=4)
        p.recalcular()
        PedidoVenda.objects.filter(pk=p.pk).update(status="faturado")
        outro = PedidoVenda.objects.create(cliente=self.cli, vendedor=self.admin)
        ItemPedidoVenda.objects.create(pedido=outro, produto=self.prod, quantidade=50)
        outro.recalcular()
        PedidoVenda.objects.filter(pk=outro.pk).update(status="faturado")
        self.assertEqual((m.realizado(), m.percentual()), (D("400.00"), D("40.0")))

    def test_pipeline_ponderado_e_projecao(self):
        m = MetaVenda.objects.create(vendedor=self.vend, competencia=HOJE, valor_meta=D("1000"))
        Oportunidade.objects.create(titulo="A", prospect_empresa="X", valor=D("1000"), estagio="proposta",
                                    responsavel=self.vend, previsao_fechamento=HOJE)
        Oportunidade.objects.create(titulo="B", prospect_empresa="Y", valor=D("1000"), estagio="ganha",
                                    responsavel=self.vend, previsao_fechamento=HOJE)
        self.assertEqual(m.pipeline(), D("500.00"))
        self.assertEqual(m.projecao(), D("500.00"))

    def test_meta_unica_e_positiva(self):
        MetaVenda.objects.create(vendedor=self.vend, competencia=HOJE, valor_meta=D("1"))
        with self.assertRaises(ValidationError):
            MetaVenda.objects.create(vendedor=self.vend, competencia=HOJE.replace(day=2), valor_meta=D("2"))
        with self.assertRaises(ValidationError):
            MetaVenda(vendedor=self.admin, competencia=HOJE, valor_meta=D("0")).full_clean()

    def test_campanha_mede_conversao_e_custo(self):
        c = Campanha.objects.create(nome="Feira", canal="evento", custo_realizado=D("1000"))
        for i, est_ in enumerate(("ganha", "perdida", "proposta", "ganha")):
            Oportunidade.objects.create(titulo=f"O{i}", prospect_empresa=f"E{i}", valor=D("500"), estagio=est_, campanha=c)
        self.assertEqual((c.leads(), c.ganhas(), c.conversao()), (4, 2, D("50.0")))
        self.assertEqual((c.valor_ganho(), c.custo_por_lead(), c.retorno()), (D("1000.00"), D("250.00"), D("1.00")))

    def test_campanha_sem_oportunidade_nao_divide_por_zero(self):
        c = Campanha.objects.create(nome="Vazia")
        self.assertEqual((c.conversao(), c.custo_por_lead(), c.retorno()), (D("0"), D("0"), None))

    def test_telas(self):
        self.client.force_login(self.admin)
        for nome in ("campanha_list", "metavenda_list", "campanha_create", "metavenda_create"):
            self.assertEqual(self.client.get(reverse(f"crm:{nome}")).status_code, 200)
        c = Campanha.objects.create(nome="C")
        self.assertEqual(self.client.get(c.get_absolute_url()).status_code, 200)
        dados = {"vendedor": self.vend.pk, "competencia": HOJE.isoformat(), "valor_meta": "500"}
        self.assertEqual(self.client.post(reverse("crm:metavenda_create"), dados).status_code, 302)
        r = self.client.post(reverse("crm:metavenda_create"), dados)      # duplicada: volta ao formulário com erro
        self.assertEqual(r.status_code, 200)
        self.assertEqual(MetaVenda.objects.count(), 1)


class SSTCipaAmbiental(Base):
    def colaborador(self, nome="Ana", cpf="52998224725"):
        return Colaborador.objects.create(nome=nome, cpf=cpf)

    def test_mandato_invalido(self):
        with self.assertRaises(ValidationError):
            MembroCIPA(colaborador=self.colaborador(), inicio=HOJE, fim=HOJE).full_clean()

    def test_alerta_de_fim_de_mandato_uma_vez(self):
        MembroCIPA.objects.create(colaborador=self.colaborador(), inicio=HOJE - datetime.timedelta(days=340),
                                  fim=HOJE + datetime.timedelta(days=20))
        ReuniaoCIPA.objects.create(data=HOJE)
        antes = Atividade.objects.count()
        self.assertEqual(alertar_vencimentos(dias=30), 1)
        self.assertEqual(alertar_vencimentos(dias=30), 0)
        self.assertEqual(Atividade.objects.count(), antes + 1)

    def test_reuniao_mensal_em_atraso(self):
        MembroCIPA.objects.create(colaborador=self.colaborador(), inicio=HOJE - datetime.timedelta(days=100),
                                  fim=HOJE + datetime.timedelta(days=265))
        self.assertEqual(alertar_vencimentos(dias=30), 1)          # nunca houve reunião
        ReuniaoCIPA.objects.create(data=HOJE - datetime.timedelta(days=10))
        Atividade.objects.all().delete()
        self.assertEqual(alertar_vencimentos(dias=30), 0)          # reunião recente: sem alerta

    def test_sem_cipa_ativa_nao_alerta_reuniao(self):
        self.assertEqual(alertar_vencimentos(dias=30), 0)

    def test_licenca_a_vencer_gera_alerta(self):
        LicencaAmbiental.objects.create(descricao="LO da fábrica", orgao="CETESB",
                                        emissao=HOJE - datetime.timedelta(days=700),
                                        validade=HOJE + datetime.timedelta(days=25))
        LicencaAmbiental.objects.create(descricao="Outra", validade=HOJE + datetime.timedelta(days=400))
        LicencaAmbiental.objects.create(descricao="Encerrada", validade=HOJE, status="encerrada")
        self.assertEqual(alertar_vencimentos(dias=30), 1)

    def test_licenca_validade_antes_da_emissao(self):
        with self.assertRaises(ValidationError):
            LicencaAmbiental(descricao="x", emissao=HOJE, validade=HOJE - datetime.timedelta(days=1)).full_clean()

    def test_residuo_perigoso_exige_mtr(self):
        with self.assertRaises(ValidationError):
            ResiduoGerado(residuo="Óleo usado", classe="I", quantidade=D("20")).full_clean()
        ResiduoGerado(residuo="Óleo usado", classe="I", quantidade=D("20"), mtr="123").full_clean()
        with self.assertRaises(ValidationError):
            ResiduoGerado(residuo="Papel", classe="IIA", quantidade=D("0")).full_clean()

    def test_telas(self):
        self.client.force_login(self.admin)
        for nome in ("membrocipa_list", "reuniaocipa_list", "licencaambiental_list", "residuogerado_list",
                     "residuogerado_create"):
            self.assertEqual(self.client.get(reverse(f"sst:{nome}")).status_code, 200)


class ProducaoRoteiro(Base):
    def setUp(self):
        self.mesa = Produto.objects.create(codigo="MESA", descricao="Mesa")
        self.est = EstruturaProduto.objects.create(produto=self.mesa)
        OperacaoRoteiro.objects.create(estrutura=self.est, sequencia=10, descricao="Corte", setup_min=D("30"),
                                       tempo_unitario_min=D("5"), custo_hora=D("60"))
        OperacaoRoteiro.objects.create(estrutura=self.est, sequencia=20, descricao="Montagem", setup_min=0,
                                       tempo_unitario_min=D("12.5"), custo_hora=D("48"))

    def test_tempo_e_custo_do_roteiro(self):
        # corte: 30 + 5×10 = 80 min × R$60/h = R$80; montagem: 125 min × R$48/h = R$100
        self.assertEqual(self.est.tempo_total_min(10), D("205.00"))
        self.assertEqual(self.est.custo_operacoes(10), D("180.00"))

    def test_ordem_mostra_tempo_previsto(self):
        op = OrdemProducao.objects.create(produto=self.mesa, quantidade=10)
        self.assertEqual((op.tempo_previsto_min(), op.custo_roteiro_previsto()), (D("205.00"), D("180.00")))

    def test_sem_roteiro_ou_estrutura_da_zero(self):
        outro = Produto.objects.create(codigo="X", descricao="Sem estrutura")
        op = OrdemProducao.objects.create(produto=outro, quantidade=3)
        self.assertEqual((op.tempo_previsto_min(), op.custo_roteiro_previsto()), (D("0"), D("0")))

    def test_telas(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("producao:operacaoroteiro_list")).status_code, 200)
        self.assertContains(self.client.get(self.est.get_absolute_url()), "Corte")


class QualidadePlano(Base):
    def setUp(self):
        self.quar = Armazem.objects.create(codigo="Q1", nome="Quarentena", tipo="quarentena")
        self.plano = PlanoInspecao.objects.create(produto=self.prod)
        self.diam = CaracteristicaInspecao.objects.create(plano=self.plano, descricao="Diâmetro", unidade="mm",
                                                          minimo=D("9.9"), maximo=D("10.1"))
        self.visual = CaracteristicaInspecao.objects.create(plano=self.plano, descricao="Acabamento",
                                                            tipo="qualitativa")

    def inspecao(self):
        est.entrada(self.prod, 10, "5", self.quar)
        return Inspecao.objects.create(produto=self.prod, quantidade=10, armazem=self.quar)

    def test_resultados_nascem_do_plano(self):
        i = self.inspecao()
        self.assertEqual(i.resultados.count(), 2)
        self.assertEqual(i.gerar_resultados(), 0)               # não duplica

    def test_pendencia_barra_a_conclusao(self):
        i = self.inspecao()
        with self.assertRaisesMessage(ValidationError, "Preencha"):
            i.concluir(self.admin, 10, "ok")

    def test_conforme_libera(self):
        i = self.inspecao()
        i.resultados.filter(caracteristica=self.diam).update(valor=D("10.05"))
        i.resultados.filter(caracteristica=self.visual).update(atendeu=True)
        self.assertEqual(i.concluir(self.admin, 10, "ok"), "aprovada")

    def test_fora_da_faixa_barra_aprovacao_total(self):
        i = self.inspecao()
        i.resultados.filter(caracteristica=self.diam).update(valor=D("10.2"))
        i.resultados.filter(caracteristica=self.visual).update(atendeu=True)
        with self.assertRaisesMessage(ValidationError, "fora da especificação"):
            i.concluir(self.admin, 10, "ok")
        self.assertEqual(i.concluir(self.admin, 6, "lote parcial"), "parcial")

    def test_qualitativa_nao_atende(self):
        i = self.inspecao()
        i.resultados.filter(caracteristica=self.diam).update(valor=D("10"))
        i.resultados.filter(caracteristica=self.visual).update(atendeu=False)
        self.assertEqual(i.concluir(self.admin, 0, "riscado"), "reprovada")

    def test_linha_apagada_continua_pendente(self):
        i = self.inspecao()
        i.resultados.filter(caracteristica=self.diam).update(valor=D("10"))
        i.resultados.filter(caracteristica=self.visual).update(atendeu=True)
        i.resultados.filter(caracteristica=self.visual).delete()
        with self.assertRaisesMessage(ValidationError, "Preencha"):
            i.concluir(self.admin, 10, "ok")

    def test_produto_sem_plano_nao_muda_nada(self):
        outro = Produto.objects.create(codigo="Z", descricao="Sem plano")
        est.entrada(outro, 5, "5", self.quar)
        i = Inspecao.objects.create(produto=outro, quantidade=5, armazem=self.quar)
        self.assertEqual(i.resultados.count(), 0)
        self.assertEqual(i.concluir(self.admin, 5, "ok"), "aprovada")

    def test_faixa_invalida(self):
        with self.assertRaises(ValidationError):
            CaracteristicaInspecao(plano=self.plano, descricao="x", minimo=D("5"), maximo=D("1")).full_clean()
        with self.assertRaises(ValidationError):
            CaracteristicaInspecao(plano=self.plano, descricao="y").full_clean()

    def test_telas(self):
        self.client.force_login(self.admin)
        i = self.inspecao()
        self.assertEqual(self.client.get(reverse("qualidade:planoinspecao_list")).status_code, 200)
        self.assertEqual(self.client.get(i.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("qualidade:inspecao_update", args=[i.pk])).status_code, 200)
