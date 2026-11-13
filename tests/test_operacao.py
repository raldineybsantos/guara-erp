"""Manutenção, Projetos, CRM e SST/EHS."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cargo, Cliente, Colaborador, Fornecedor
from apps.crm.models import Interacao, Oportunidade
from apps.estoque import models as est
from apps.estoque.models import Armazem, Saldo
from apps.financeiro.models import CentroCusto, ContaPagar
from apps.fiscal.models import Produto
from apps.manutencao.models import Equipamento, OrdemServico, PecaOS, PlanoManutencao, gerar_preventivas
from apps.projetos.models import ApontamentoHoras, Projeto, TarefaProjeto
from apps.sst.models import AvaliacaoRisco, EntregaEPI, ExameOcupacional, Incidente, TreinamentoSST, alertar_vencimentos
from apps.tarefas.models import Atividade
from apps.vendas.models import PedidoVenda


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.joao = User.objects.create_user("joao", password="x", first_name="João")
        cls.joao.groups.add(Group.objects.get(name="Usuários"))
        cls.a1 = Armazem.objects.create(codigo="01", nome="Almox", padrao=True)
        cls.cargo = Cargo.objects.create(nome="Analista", salario_base=3000)
        cls.colab = Colaborador.objects.create(nome="João Silva", cpf="52998224725", usuario=cls.joao,
                                               cargo=cls.cargo, salario=4400)

    def grupo(self, nome, usuario="u"):
        u = User.objects.create_user(usuario, password="x")
        u.groups.add(Group.objects.get(name=nome))
        return u


class ManutencaoTest(Base):
    def test_preventiva_gerada_e_reprogramada(self):
        eq = Equipamento.objects.create(codigo="CMP-01", nome="Compressor", critico=True)
        hoje = timezone.localdate()
        plano = PlanoManutencao.objects.create(equipamento=eq, descricao="Troca de óleo", periodicidade_dias=90,
                                               proxima=hoje + datetime.timedelta(days=3), checklist="Drenar")
        self.assertEqual(gerar_preventivas(antecedencia_dias=7), 1)
        self.assertEqual(gerar_preventivas(antecedencia_dias=7), 0)  # já há OS aberta
        os_ = OrdemServico.objects.get()
        self.assertEqual((os_.tipo, os_.plano), ("preventiva", plano))
        oleo = Produto.objects.create(codigo="OLEO", descricao="Óleo")
        est.entrada(oleo, 10, "30", self.a1)
        PecaOS.objects.create(ordem=os_, produto=oleo, quantidade=2)
        with self.assertRaises(ValidationError):
            os_.concluir("  ")
        os_.concluir("Óleo trocado", horas=1.5, custo_mao_obra=80)
        os_.refresh_from_db()
        self.assertEqual((os_.status, os_.custo_pecas, os_.custo_total), ("concluida", Decimal("60.00"),
                                                                          Decimal("140.00")))
        self.assertEqual(Saldo.objects.get(produto=oleo).quantidade, Decimal("8"))
        plano.refresh_from_db()
        self.assertEqual(plano.proxima, hoje + datetime.timedelta(days=90))

    def test_qualquer_um_abre_corretiva(self):
        eq = Equipamento.objects.create(codigo="AR-01", nome="Ar-condicionado")
        self.client.force_login(self.joao)
        r = self.client.post(reverse("manutencao:ordemservico_create"), {
            "equipamento": eq.pk, "tipo": "corretiva", "prioridade": "alta", "descricao": "Não gela",
            "pecas-TOTAL_FORMS": "0", "pecas-INITIAL_FORMS": "0"})
        self.assertEqual(r.status_code, 302, r.content[:2000])
        self.assertEqual(OrdemServico.objects.get().solicitante, self.joao)
        self.assertEqual(self.client.get(reverse("manutencao:concluir", args=[1])).status_code, 403)
        tec = self.grupo("Manutenção", "tec")
        self.client.force_login(tec)
        os_ = OrdemServico.objects.get()
        self.client.post(reverse("manutencao:iniciar", args=[os_.pk]))
        self.client.post(reverse("manutencao:concluir", args=[os_.pk]), {"servico_executado": "Gás recarregado"})
        os_.refresh_from_db()
        self.assertEqual(os_.status, "concluida")
        self.assertGreaterEqual(eq.horas_parado(), 0)
        for n in ("ordemservico", "planomanutencao", "equipamento"):
            self.assertEqual(self.client.get(reverse(f"manutencao:{n}_list")).status_code, 200)
        self.assertEqual(self.client.get(eq.get_absolute_url()).status_code, 200)


class ProjetosTest(Base):
    def test_custos_e_avanco(self):
        cc = CentroCusto.objects.create(codigo="PRJ1", nome="Projeto 1")
        p = Projeto.objects.create(nome="Implantação", gerente=self.admin, orcamento=10000, centro_custo=cc,
                                   status="andamento")
        t1 = TarefaProjeto.objects.create(projeto=p, eap="1", nome="Levantamento", horas_previstas=10, percentual=100)
        TarefaProjeto.objects.create(projeto=p, eap="2", nome="Configuração", horas_previstas=30, percentual=0)
        self.assertEqual(p.percentual_concluido(), Decimal("25.00"))
        from apps.core.middleware import _local

        _local.usuario = self.joao
        try:
            a = ApontamentoHoras.objects.create(projeto=p, tarefa=t1, horas=8, descricao="Entrevistas")
        finally:
            _local.usuario = None
        self.assertEqual((a.usuario, a.custo_hora, a.custo), (self.joao, Decimal("20.00"), Decimal("160.00")))
        ContaPagar.objects.create(descricao="Viagem", valor=500, vencimento="2026-12-01", centro_custo=cc)
        self.assertEqual(p.custo_realizado(), Decimal("660.00"))
        self.assertEqual(p.saldo_orcamento(), Decimal("9340.00"))

    def test_cada_um_ve_as_proprias_horas(self):
        p = Projeto.objects.create(nome="P", gerente=self.admin, status="andamento")
        outro = User.objects.create_user("maria", password="x")
        ApontamentoHoras.objects.create(projeto=p, horas=2, descricao="da Maria", usuario=outro)
        self.client.force_login(self.joao)
        r = self.client.post(reverse("projetos:apontamentohoras_create"),
                             {"projeto": p.pk, "data": "2026-11-13", "horas": "3", "descricao": "meu"})
        self.assertEqual(r.status_code, 302, r.content[:2000])
        r = self.client.get(reverse("projetos:apontamentohoras_list"))
        self.assertContains(r, "meu")
        self.assertNotContains(r, "da Maria")
        alheio = ApontamentoHoras.objects.get(descricao="da Maria")
        self.assertEqual(self.client.get(reverse("projetos:apontamentohoras_update", args=[alheio.pk])).status_code,
                         404)
        r = self.client.post(reverse("projetos:apontamentohoras_create"),
                             {"projeto": p.pk, "data": "2026-11-13", "horas": "25", "descricao": "x"})
        self.assertContains(r, "entre 0 e 24")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(p.get_absolute_url()).status_code, 200)


class CrmTest(Base):
    def test_funil_e_ganho_de_prospect(self):
        vend = self.grupo("Vendas", "vend")
        op = Oportunidade.objects.create(titulo="ERP para a Acme", prospect_empresa="Acme Ltda",
                                         prospect_documento="11.222.333/0001-81", valor=50000, responsavel=vend)
        self.assertEqual((op.probabilidade, op.valor_ponderado), (10, Decimal("5000.00")))
        op.mover("negociacao")
        self.assertEqual(op.probabilidade, 75)
        Interacao.objects.create(oportunidade=op, resumo="Reunião", proximo_passo="Enviar proposta revisada",
                                 data_proximo_passo=timezone.localdate(), usuario=vend)
        self.assertTrue(Atividade.objects.filter(responsavel=vend, etiquetas="crm").exists())
        pv = op.ganhar(vend)
        cli = Cliente.objects.get(documento="11222333000181")
        self.assertEqual((op.estagio, op.cliente, pv.cliente, pv.status), ("ganha", cli, cli, "rascunho"))
        with self.assertRaises(ValidationError):
            op.perder("tarde demais")

    def test_ganhar_reaproveita_parceiro_e_perder_exige_motivo(self):
        Fornecedor.objects.create(nome="Beta", documento="11444777000161")
        op = Oportunidade.objects.create(titulo="x", prospect_empresa="Beta", prospect_documento="11444777000161")
        op.ganhar()
        self.assertEqual(op.cliente.parceironegocio.fornecedor.nome, "Beta")
        op2 = Oportunidade.objects.create(titulo="y", prospect_empresa="Gama")
        with self.assertRaises(ValidationError):
            op2.perder(" ")
        op2.perder("Preço")
        self.assertEqual(op2.probabilidade, 0)

    def test_telas(self):
        vend = self.grupo("Vendas", "vend")
        self.client.force_login(vend)
        r = self.client.post(reverse("crm:oportunidade_create"), {"titulo": "Sem conta", "origem": "site",
                                                                  "estagio": "prospeccao", "valor": "100"})
        self.assertContains(r, "Informe o cliente")
        cli = Cliente.objects.create(nome="Cliente C")
        op = Oportunidade.objects.create(titulo="Renovação", cliente=cli, valor=1000, estagio="proposta")
        r = self.client.get(reverse("crm:funil"))
        self.assertContains(r, "Renovação")
        self.client.post(reverse("crm:funil"), {"oportunidade": op.pk, "estagio": "negociacao"})
        op.refresh_from_db()
        self.assertEqual(op.estagio, "negociacao")
        r = self.client.post(reverse("crm:ganhar", args=[op.pk]))
        self.assertRedirects(r, PedidoVenda.objects.get().get_absolute_url())
        self.assertEqual(self.client.get(op.get_absolute_url()).status_code, 200)


class SstTest(Base):
    def test_incidente_cat_e_privacidade(self):
        sesmt = self.grupo("SESMT", "sesmt")
        inc = Incidente.objects.create(tipo="sem_afastamento", local="Prensa 2", descricao="Corte na mão",
                                       criado_por=self.joao)
        tarefa = Atividade.objects.get(object_id=inc.pk, etiquetas="sst")
        self.assertEqual((tarefa.prioridade, tarefa.grupo.name), ("urgente", "SESMT"))
        inc.status, inc.causas, inc.acoes = "encerrado", "Sem luva", "Luva anticorte"
        with self.assertRaisesMessage(ValidationError, "CAT"):
            inc.full_clean()
        Incidente.objects.create(tipo="quase", local="Escada", descricao="Degrau solto", criado_por=sesmt)
        self.client.force_login(self.joao)
        r = self.client.get(reverse("sst:incidente_list"))
        self.assertContains(r, inc.numero)
        self.assertNotContains(r, "Escada")
        self.assertEqual(self.client.get(reverse("sst:exameocupacional_list")).status_code, 403)
        self.client.force_login(sesmt)
        self.assertContains(self.client.get(reverse("sst:incidente_list")), "Escada")

    def test_risco_epi_e_alertas(self):
        r = AvaliacaoRisco.objects.create(atividade="Solda", perigo="Fumos metálicos", tipo="quimico",
                                          probabilidade=4, severidade=4, medidas_controle="Exaustão")
        self.assertEqual((r.nivel, r.classificacao()), (16, "Substancial"))
        luva = Produto.objects.create(codigo="LUVA", descricao="Luva")
        est.entrada(luva, 10, "5", self.a1)
        EntregaEPI.objects.create(colaborador=self.colab, descricao="Luva anticorte", ca="12345", produto=luva,
                                  quantidade=2)
        self.assertEqual(Saldo.objects.get(produto=luva).quantidade, Decimal("8"))
        hoje = timezone.localdate()
        ExameOcupacional.objects.create(colaborador=self.colab, tipo="periodico", resultado="apto",
                                        data=hoje - datetime.timedelta(days=350),
                                        validade=hoje + datetime.timedelta(days=15))
        TreinamentoSST.objects.create(colaborador=self.colab, norma="NR-35", descricao="Altura",
                                      validade=hoje + datetime.timedelta(days=200))
        self.assertEqual(alertar_vencimentos(dias=30), 1)
        self.assertEqual(alertar_vencimentos(dias=30), 0)
        out = __import__("io").StringIO()
        call_command("rotinas_diarias", stdout=out)
        self.assertIn("SST", out.getvalue())

    def test_telas(self):
        sesmt = self.grupo("SESMT", "sesmt")
        self.client.force_login(sesmt)
        for n in ("incidente", "avaliacaorisco", "entregaepi", "exameocupacional", "treinamentosst"):
            self.assertEqual(self.client.get(reverse(f"sst:{n}_list")).status_code, 200, n)
            self.assertEqual(self.client.get(reverse(f"sst:{n}_create")).status_code, 200, n)
