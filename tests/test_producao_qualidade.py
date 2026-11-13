from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.compras.models import ItemPedido, PedidoCompra, SolicitacaoCompra
from apps.core.models import Fornecedor
from apps.estoque import models as est
from apps.estoque.models import Armazem, Saldo
from apps.fiscal.models import Produto
from apps.producao.models import ComponenteEstrutura, EstruturaProduto, OrdemProducao
from apps.qualidade.models import Inspecao, NaoConformidade
from apps.tarefas.models import Atividade


class ProducaoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.pcp = User.objects.create_user("pcp", password="x")
        cls.pcp.groups.add(Group.objects.get(name="Produção"))
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.mesa = Produto.objects.create(codigo="MESA", descricao="Mesa")
        cls.tampo = Produto.objects.create(codigo="TAMPO", descricao="Tampo")
        cls.pe = Produto.objects.create(codigo="PE", descricao="Pé")
        e = EstruturaProduto.objects.create(produto=cls.mesa)
        ComponenteEstrutura.objects.create(estrutura=e, componente=cls.tampo, quantidade=1)
        ComponenteEstrutura.objects.create(estrutura=e, componente=cls.pe, quantidade=4, perda_pct=5)

    def test_apontamento_consome_e_custeia(self):
        est.entrada(self.tampo, 10, "100", self.a1)
        est.entrada(self.pe, 50, "10", self.a1)
        op = OrdemProducao.objects.create(produto=self.mesa, quantidade=5)
        op.liberar()
        unit = op.apontar(2, self.pcp)
        # 1 tampo (100) + 4,2 pés (42) = 142 por mesa
        self.assertEqual(unit, Decimal("142.0000"))
        self.assertEqual(Saldo.objects.get(produto=self.pe).quantidade, Decimal("41.6"))
        s = Saldo.objects.get(produto=self.mesa)
        self.assertEqual((s.quantidade, s.custo_medio), (Decimal("2"), Decimal("142")))
        op.apontar(3)
        op.refresh_from_db()
        self.assertEqual((op.status, op.custo_total, op.custo_unitario), ("encerrada", Decimal("710.00"),
                                                                           Decimal("142.0000")))

    def test_falta_de_componente_e_mrp(self):
        est.entrada(self.tampo, 1, "100", self.a1)
        op = OrdemProducao.objects.create(produto=self.mesa, quantidade=3)
        with self.assertRaisesMessage(ValidationError, "Libere"):
            op.apontar(1)
        op.liberar()
        with self.assertRaisesMessage(ValidationError, "Faltam componentes"):
            op.apontar(3)
        sc = op.gerar_solicitacao_compra(self.pcp)
        faltas = {i.produto.codigo: i.quantidade for i in sc.itens.all()}
        self.assertEqual(faltas, {"TAMPO": Decimal("2"), "PE": Decimal("12.6")})

    def test_sem_estrutura_nao_libera(self):
        op = OrdemProducao.objects.create(produto=self.tampo, quantidade=1)
        with self.assertRaisesMessage(ValidationError, "estrutura"):
            op.liberar()

    def test_telas(self):
        est.entrada(self.tampo, 10, "100", self.a1)
        self.client.force_login(self.pcp)
        r = self.client.post(reverse("producao:ordemproducao_create"), {"produto": self.mesa.pk, "quantidade": "2"})
        op = OrdemProducao.objects.get()
        self.assertContains(self.client.get(op.get_absolute_url()), "Componentes para o saldo")
        self.client.post(reverse("producao:liberar", args=[op.pk]))
        r = self.client.post(reverse("producao:apontar", args=[op.pk]), {"quantidade": "1"})
        self.assertContains(r, "Faltam componentes")
        r = self.client.post(reverse("producao:gerar_sc", args=[op.pk]))
        self.assertRedirects(r, SolicitacaoCompra.objects.get().get_absolute_url(), fetch_redirect_response=False)
        for nome in ("ordemproducao", "estruturaproduto"):
            self.assertEqual(self.client.get(reverse(f"producao:{nome}_list")).status_code, 200)


class QualidadeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.insp = User.objects.create_user("insp", password="x", first_name="Ivo")
        cls.insp.groups.add(Group.objects.get(name="Qualidade"))
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.forn = Fornecedor.objects.create(nome="Forn")
        cls.resina = Produto.objects.create(codigo="RES", descricao="Resina", inspecionar_recebimento=True)

    def _receber(self, qtd=100):
        pc = PedidoCompra.objects.create(fornecedor=self.forn, status="aprovado")
        item = ItemPedido.objects.create(pedido=pc, produto=self.resina, quantidade=qtd, valor_unitario=5)
        pc.receber({str(item.pk): qtd})
        return Inspecao.objects.get()

    def test_recebimento_vai_para_quarentena_e_aprovacao_libera(self):
        ins = self._receber()
        qua = Armazem.quarentena()
        self.assertEqual((ins.armazem, ins.fornecedor, ins.status), (qua, self.forn, "pendente"))
        self.assertEqual(est.saldo_disponivel(self.resina), 0)  # quarentena não conta como disponível
        self.assertEqual(ins.concluir(self.insp, Decimal("100"), "Laudo ok"), "aprovada")
        self.assertEqual(est.saldo_disponivel(self.resina), Decimal("100"))
        self.assertEqual(Saldo.objects.get(produto=self.resina, armazem=self.a1).custo_medio, Decimal("5"))

    def test_reprovacao_parcial_abre_nc_e_tarefa(self):
        ins = self._receber()
        self.assertEqual(ins.concluir(self.insp, Decimal("80"), "20 baldes amassados"), "parcial")
        nc = ins.nao_conformidade
        self.assertEqual((nc.fornecedor, nc.origem, nc.responsavel), (self.forn, "recebimento", self.insp))
        self.assertIn("20 Resina", nc.descricao.replace(" UN", " Resina") or nc.descricao)
        self.assertEqual(Saldo.objects.get(produto=self.resina, armazem=Armazem.quarentena()).quantidade,
                         Decimal("20"))
        self.assertTrue(Atividade.objects.filter(object_id=nc.pk, responsavel=self.insp).exists())
        with self.assertRaises(ValidationError):
            ins.concluir(self.insp, 1, "de novo")

    def test_nc_so_encerra_com_causa_acao_e_eficacia(self):
        nc = NaoConformidade.objects.create(titulo="Reclamação", descricao="x", gravidade="critica")
        self.assertEqual((nc.prazo - nc.criado_em.date()).days, 2)
        nc.status = "encerrada"
        with self.assertRaisesMessage(ValidationError, "causa raiz"):
            nc.full_clean()
        nc.causa_raiz, nc.acao_corretiva, nc.verificacao_eficacia = "a", "b", "c"
        nc.full_clean()
        nc.save()
        self.assertIsNotNone(nc.encerrada_em)

    def test_telas(self):
        ins = self._receber(10)
        self.client.force_login(self.insp)
        r = self.client.post(reverse("qualidade:concluir_inspecao", args=[ins.pk]),
                             {"quantidade_aprovada": "0", "resultado": "Lote contaminado"})
        self.assertRedirects(r, ins.get_absolute_url())
        ins.refresh_from_db()
        self.assertEqual(ins.status, "reprovada")
        for nome in ("inspecao", "naoconformidade"):
            self.assertEqual(self.client.get(reverse(f"qualidade:{nome}_list")).status_code, 200)
        self.assertEqual(self.client.get(ins.nao_conformidade.get_absolute_url()).status_code, 200)
