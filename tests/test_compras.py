"""Compras: solicitação → cotação → pedido com alçada → recebimento → conferência em três vias."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.compras.models import AlcadaCompra, ItemSolicitacao, PedidoCompra, SolicitacaoCompra
from apps.core.models import Departamento, Fornecedor
from apps.workflow.models import Instancia
from apps.financeiro.models import ContaPagar
from apps.fiscal.models import ItemNota, NotaFiscal, Produto
from apps.tarefas.models import Atividade


@override_settings(COMPRAS_TOLERANCIA_PRECO_PCT="2")
class ComprasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.comprador = User.objects.create_user("comprador", password="x")
        cls.comprador.groups.add(Group.objects.get(name="Compras"))
        cls.usuario = User.objects.create_user("usuario", password="x")
        cls.usuario.groups.add(Group.objects.get(name="Usuários"))
        cls.coord, cls.ger, cls.dir = (User.objects.create_user(n, password="x", first_name=n.capitalize())
                                       for n in ("coordenadora", "gerente", "diretora"))
        for u in (cls.coord, cls.ger, cls.dir):
            u.groups.add(Group.objects.get(name="Usuários"))
        cls.alcada = AlcadaCompra.objects.create(coordenador=cls.coord, limite_coordenador=1000, gerente=cls.ger,
                                                 limite_gerente=10000, diretor=cls.dir)
        cls.papel = Produto.objects.create(codigo="PAP-A4", descricao="Papel A4 resma")
        cls.toner = Produto.objects.create(codigo="TON-01", descricao="Toner preto")
        cls.f1 = Fornecedor.objects.create(nome="Papelaria Um", documento="11444777000161", prazo_pagamento_dias=28)
        cls.f2 = Fornecedor.objects.create(nome="Papelaria Dois")

    def _sc(self, por=None):
        sc = SolicitacaoCompra.objects.create(justificativa="Reposição", solicitante=por or self.usuario)
        ItemSolicitacao.objects.create(solicitacao=sc, produto=self.papel, quantidade=10)
        ItemSolicitacao.objects.create(solicitacao=sc, produto=self.toner, quantidade=2)
        return sc

    def _pedido(self, preco_papel="25", preco_toner="300"):
        cot = self._sc().gerar_cotacao([self.f1, self.f2])
        for item in cot.itens.filter(fornecedor=self.f1):
            item.valor_unitario = Decimal(preco_papel if item.produto == self.papel else preco_toner)
            item.prazo_entrega_dias = 5
            item.save()
        return cot.escolher(self.f1, self.comprador)

    def _nota(self, pedido, itens, numero=123):
        nota = NotaFiscal.objects.create(tipo="entrada", fornecedor=pedido.fornecedor, numero=numero,
                                         status="emitida", natureza_operacao="Compra")
        for produto, qtd, preco in itens:
            ItemNota.objects.create(nota=nota, produto=produto, quantidade=qtd, valor_unitario=preco)
        nota.recalcular()
        return nota

    # ---------------------------------------------------------------- solicitação e cotação
    def test_numeracao_e_cotacao_com_mapa(self):
        sc = self._sc()
        self.assertRegex(sc.numero, r"^SC-\d{4}-0001$")
        cot = sc.gerar_cotacao([self.f1, self.f2])
        self.assertEqual(cot.itens.count(), 4)
        sc.refresh_from_db()
        self.assertEqual(sc.status, "em_cotacao")
        precos = {(self.f1, self.papel): 25, (self.f1, self.toner): 300, (self.f2, self.papel): 24}
        for item in cot.itens.all():
            item.valor_unitario = precos.get((item.fornecedor, item.produto), 0)
            item.save()
        mapa = cot.mapa()
        # f2 é mais barato no papel, mas não cotou o toner: o completo vem primeiro.
        self.assertEqual(mapa[0]["fornecedor"], self.f1)
        self.assertTrue(mapa[0]["melhor"])
        self.assertFalse(mapa[1]["completo"])

    def test_escolher_gera_pedido_e_atende_solicitacao(self):
        pedido = self._pedido()
        self.assertEqual(pedido.valor_total, Decimal("850.00"))
        self.assertEqual(pedido.itens.count(), 2)
        self.assertEqual(pedido.solicitacao.status, "atendida")
        self.assertEqual(pedido.cotacao.status, "concluida")
        self.assertEqual(pedido.previsao_entrega - pedido.data_pedido, datetime.timedelta(days=5))

    # ---------------------------------------------------------------- alçada
    def _aprovar_tudo(self, pedido):
        inst = Instancia.objects.get(object_id=pedido.pk, status="andamento")
        quem = []
        while inst.status == "andamento":
            quem.append(inst.responsavel_atual.username)
            inst.avancar(inst.responsavel_atual, "aprovar")
        pedido.refresh_from_db()
        return quem

    def test_pedido_pequeno_so_coordenador(self):
        pedido = self._pedido(preco_papel="10", preco_toner="100")  # 300
        inst = pedido.enviar_para_aprovacao(self.comprador)
        self.assertEqual(pedido.status, "em_aprovacao")
        self.assertEqual(inst.responsavel_atual, self.coord)
        # Só a coordenadora designada atua; o comprador (nem o grupo Compras) não.
        with self.assertRaises(ValidationError):
            inst.avancar(self.comprador, "aprovar")
        self.assertIn(inst, Instancia.objects.pendentes_para(self.coord))
        self.assertNotIn(inst, Instancia.objects.pendentes_para(self.comprador))
        self.assertNotIn(inst, Instancia.objects.pendentes_para(self.ger))
        self.assertEqual(self._aprovar_tudo(pedido), ["coordenadora"])
        self.assertEqual(pedido.status, "aprovado")
        self.assertIsNone(pedido.aprovador_gerente)

    def test_acima_do_coordenador_sobe_ao_gerente_e_recusa(self):
        pedido = self._pedido(preco_toner="600")  # 1.450
        inst = pedido.enviar_para_aprovacao(self.comprador)
        inst.avancar(self.coord, "aprovar")
        inst.refresh_from_db()
        self.assertEqual(inst.responsavel_atual, self.ger)
        inst.avancar(self.ger, "rejeitar", "Preço do toner alto")
        pedido.refresh_from_db()
        self.assertEqual((pedido.status, pedido.motivo_status), ("recusado", "Preço do toner alto"))

    def test_acima_do_gerente_vai_ao_diretor(self):
        pedido = self._pedido(preco_toner="6000")  # 12.250
        pedido.enviar_para_aprovacao(self.comprador)
        self.assertEqual(self._aprovar_tudo(pedido), ["coordenadora", "gerente", "diretora"])
        self.assertEqual(pedido.status, "aprovado")

    def test_limite_zero_exige_os_tres_niveis(self):
        AlcadaCompra.objects.filter(pk=self.alcada.pk).update(limite_coordenador=0, limite_gerente=0)
        pedido = self._pedido(preco_papel="1", preco_toner="1")  # 12
        pedido.enviar_para_aprovacao(self.comprador)
        self.assertEqual(self._aprovar_tudo(pedido), ["coordenadora", "gerente", "diretora"])

    def test_alcada_do_departamento_prevalece_e_mesma_pessoa_aprova_uma_vez(self):
        ti = Departamento.objects.create(codigo="TI", nome="TI")
        AlcadaCompra.objects.create(departamento=ti, coordenador=self.ger, limite_coordenador=0, gerente=self.ger,
                                    limite_gerente=0, diretor=self.dir)
        pedido = self._pedido(preco_papel="10", preco_toner="100")
        PedidoCompra.objects.filter(pk=pedido.pk).update(departamento=ti)
        pedido.refresh_from_db()
        pedido.enviar_para_aprovacao(self.comprador)
        self.assertEqual(self._aprovar_tudo(pedido), ["gerente", "diretora"])

    def test_departamento_vem_da_solicitacao(self):
        rh = Departamento.objects.create(codigo="RHX", nome="RH")
        sc = self._sc()
        SolicitacaoCompra.objects.filter(pk=sc.pk).update(departamento=rh)
        sc.refresh_from_db()
        cot = sc.gerar_cotacao([self.f1])
        cot.itens.update(valor_unitario=5)
        self.assertEqual(cot.escolher(self.f1).departamento, rh)

    def test_sem_alcada_cadastrada(self):
        AlcadaCompra.objects.all().delete()
        pedido = self._pedido()
        with self.assertRaisesMessage(ValidationError, "Alçadas de aprovação"):
            pedido.enviar_para_aprovacao(self.comprador)
        self.assertEqual(pedido.status, "rascunho")

    def test_tela_de_alcadas(self):
        url = reverse("compras:alcadacompra_update", args=[self.alcada.pk])
        dados = {"coordenador": self.coord.pk, "limite_coordenador": "2000", "gerente": self.ger.pk,
                 "limite_gerente": "1500", "diretor": self.dir.pk}
        # Quem compra não altera quem aprova.
        self.client.force_login(self.comprador)
        self.assertEqual(self.client.get(reverse("compras:alcadacompra_list")).status_code, 200)
        self.assertEqual(self.client.post(url, dados).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(url, dados), "não pode ser menor")
        dados["limite_gerente"] = "20000"
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        self.alcada.refresh_from_db()
        self.assertEqual(self.alcada.limite_coordenador, Decimal("2000"))
        self.assertContains(self.client.get(reverse("compras:alcadacompra_list")), "Padrão da empresa")
        # Só existe uma alçada padrão.
        r = self.client.post(reverse("compras:alcadacompra_create"), {**dados})
        self.assertEqual(AlcadaCompra.objects.count(), 1)
        self.assertContains(r, "Já existe alçada")

    def test_pedido_aprovado_nao_pode_ser_editado(self):
        pedido = self._aprovado()
        self.client.force_login(self.admin)
        r = self.client.post(reverse("compras:pedidocompra_update", args=[pedido.pk]), {
            "fornecedor": self.f2.pk, "data_pedido": "2026-11-13", "condicao_pagamento": "30", "valor_frete": "0",
            "itens-TOTAL_FORMS": "0", "itens-INITIAL_FORMS": "0"})
        self.assertContains(r, "não pode ser alterado")
        pedido.refresh_from_db()
        self.assertEqual(pedido.fornecedor, self.f1)

    # ---------------------------------------------------------------- recebimento e três vias
    def _aprovado(self):
        pedido = self._pedido(preco_papel="10", preco_toner="100")  # 300: só a coordenadora
        pedido.enviar_para_aprovacao(self.comprador)
        self._aprovar_tudo(pedido)
        return pedido

    def _qtd(self, pedido, papel, toner):
        return {str(i.pk): (papel if i.produto == self.papel else toner) for i in pedido.itens.all()}

    def test_tres_vias_ok_libera_pagamento(self):
        pedido = self._aprovado()
        nota = self._nota(pedido, [(self.papel, 10, "10"), (self.toner, 2, "100.50")])  # dentro de 2%
        rec = pedido.receber(self._qtd(pedido, 10, 2), nota_fiscal=nota, usuario=self.comprador)
        self.assertEqual(rec.conferencia, "ok", rec.divergencias)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "recebido")
        conta = ContaPagar.objects.get(pk=rec.conta_pagar_id)
        self.assertTrue(conta.aprovado)
        # condição do pedido = prazo do fornecedor (28 dias)
        self.assertEqual(conta.vencimento, nota.data_emissao + datetime.timedelta(days=28))
        self.assertIn(pedido.numero, conta.observacoes)

    def test_condicao_parcelada_gera_uma_conta_por_parcela(self):
        pedido = self._aprovado()
        PedidoCompra.objects.filter(pk=pedido.pk).update(condicao_pagamento="30/60/90")
        pedido.refresh_from_db()
        nota = self._nota(pedido, [(self.papel, 10, "10"), (self.toner, 2, "100")])
        pedido.receber(self._qtd(pedido, 10, 2), nota_fiscal=nota)
        contas = ContaPagar.objects.filter(documento="123").order_by("parcela")
        self.assertEqual([c.valor for c in contas], [Decimal("100.00")] * 3)
        self.assertEqual([c.vencimento - nota.data_emissao for c in contas],
                         [datetime.timedelta(days=d) for d in (30, 60, 90)])
        self.assertTrue(all(c.aprovado and c.total_parcelas == 3 for c in contas))

    def test_divergencia_bloqueia_e_abre_tarefa(self):
        pedido = self._aprovado()
        nota = self._nota(pedido, [(self.papel, 10, "12"), (self.toner, 2, "100")])  # papel 20% acima
        rec = pedido.receber(self._qtd(pedido, 10, 2), nota_fiscal=nota)
        self.assertEqual(rec.conferencia, "divergente")
        self.assertIn("Papel A4", rec.divergencias)
        self.assertFalse(ContaPagar.objects.get(pk=rec.conta_pagar_id).aprovado)
        tarefa = Atividade.objects.get(object_id=rec.pk)
        self.assertEqual((tarefa.grupo.name, tarefa.prioridade), ("Compras", "alta"))
        # Conferir de novo não duplica a tarefa.
        rec.conferir()
        self.assertEqual(Atividade.objects.filter(object_id=rec.pk).count(), 1)

    def test_recebimento_parcial_sem_nota_e_nota_depois(self):
        pedido = self._aprovado()
        rec = pedido.receber(self._qtd(pedido, 4, 0))
        self.assertEqual(rec.conferencia, "pendente")
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "parcial")
        with self.assertRaises(ValidationError):
            pedido.receber(self._qtd(pedido, 7, 0))  # saldo é 6
        # A nota chega depois e é vinculada pela tela do recebimento.
        nota = self._nota(pedido, [(self.papel, 4, "10")])
        self.client.force_login(self.comprador)
        r = self.client.post(reverse("compras:recebimentocompra_update", args=[rec.pk]),
                             {"nota_fiscal": nota.pk, "observacoes": ""})
        self.assertEqual(r.status_code, 302)
        rec.refresh_from_db()
        self.assertEqual(rec.conferencia, "ok", rec.divergencias)

    def test_quantidade_faturada_maior_que_recebida(self):
        pedido = self._aprovado()
        nota = self._nota(pedido, [(self.papel, 10, "10"), (self.toner, 2, "100")])
        rec = pedido.receber(self._qtd(pedido, 8, 2), nota_fiscal=nota)
        self.assertEqual(rec.conferencia, "divergente")
        self.assertIn("nota fatura 10", rec.divergencias)

    # ---------------------------------------------------------------- telas
    def test_telas_do_fluxo(self):
        self.client.force_login(self.comprador)
        sc = self._sc()
        r = self.client.post(reverse("compras:gerar_cotacao", args=[sc.pk]),
                             {"fornecedores": [self.f1.pk, self.f2.pk]})
        cot = sc.cotacoes.get()
        self.assertRedirects(r, reverse("compras:cotacao_update", args=[cot.pk]))
        cot.itens.filter(fornecedor=self.f1).update(valor_unitario=10)
        self.assertContains(self.client.get(reverse("compras:mapa_cotacao", args=[cot.pk])), "Menor preço")
        r = self.client.post(reverse("compras:mapa_cotacao", args=[cot.pk]), {"fornecedor": self.f1.pk})
        pedido = PedidoCompra.objects.get()
        self.assertRedirects(r, pedido.get_absolute_url())
        self.client.post(reverse("compras:enviar_pedido", args=[pedido.pk]))
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "em_aprovacao")
        inst = Instancia.objects.get(object_id=pedido.pk)
        self.client.force_login(self.coord)
        self.assertContains(self.client.get(inst.get_absolute_url()), "Coordenadora")
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "aprovar"})
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "aprovado")
        self.client.force_login(self.comprador)
        pdf = self.client.get(reverse("compras:pdf_pedido", args=[pedido.pk]))
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertContains(self.client.get(reverse("compras:receber_pedido", args=[pedido.pk])), "saldo 10")
        campos = {f"item_{i.pk}": str(i.quantidade) for i in pedido.itens.all()}
        r = self.client.post(reverse("compras:receber_pedido", args=[pedido.pk]),
                             {"data": "2026-11-13", **campos})
        self.assertEqual(r.status_code, 302)
        pedido.refresh_from_db()
        self.assertEqual(pedido.status, "recebido")
        for nome in ("solicitacaocompra", "cotacao", "pedidocompra", "recebimentocompra"):
            self.assertEqual(self.client.get(reverse(f"compras:{nome}_list")).status_code, 200, nome)

    def test_usuario_comum_ve_so_as_proprias_solicitacoes(self):
        minha = self._sc(por=self.usuario)
        outra = self._sc(por=self.comprador)
        self.client.force_login(self.usuario)
        r = self.client.get(reverse("compras:solicitacaocompra_list"))
        self.assertContains(r, minha.numero)
        self.assertNotContains(r, outra.numero)
        self.assertEqual(self.client.get(reverse("compras:pedidocompra_list")).status_code, 403)
        # e pode criar uma solicitação
        r = self.client.post(reverse("compras:solicitacaocompra_create"), {
            "justificativa": "Cadeira quebrada", "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0",
            "itens-0-descricao": "Cadeira de escritório", "itens-0-quantidade": "1", "itens-0-unidade": "UN"})
        self.assertEqual(r.status_code, 302, r.content[:3000])
        nova = SolicitacaoCompra.objects.latest("pk")
        self.assertEqual((nova.solicitante, nova.itens.count()), (self.usuario, 1))
