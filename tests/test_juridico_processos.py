import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.contabilidade import servicos
from apps.contabilidade.models import ContaContabil
from apps.financeiro.models import ContaPagar
from apps.juridico.models import processar_vencimentos
from apps.juridico.processos import Andamento, Envolvido, Prazo, Processo
from apps.tarefas.models import Atividade


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.adv = User.objects.create_user("adv", password="x")
        cls.adv.groups.add(Group.objects.get(name="Jurídico"))
        cls.hoje = timezone.localdate()

    def processo(self, numero="0001234-56.2026.5.02.0001", **kw):
        return Processo.objects.create(numero=numero, assunto="Horas extras", contraparte="Ex-funcionário",
                                       natureza="trabalhista", valor_causa=D("80000"), **kw)

    def saldo(self, codigo):
        c = ContaContabil.objects.get(codigo=codigo)
        d, cr = servicos.saldos().get(c.pk, (D(0), D(0)))
        return servicos.saldo_natural(c, d, cr)


class ProcessoTest(Base):
    def test_numero_unico(self):
        self.processo()
        with self.assertRaises(Exception):
            self.processo()

    def test_risco_provavel_constitui_e_reduz_reverte(self):
        p = self.processo()
        p.avaliar_risco("possivel", D("50000"))
        self.assertEqual((p.provisao, self.saldo("2.1.4")), (D("0"), D("0")))     # possível: só divulgar
        p.avaliar_risco("provavel", D("50000"), motivo="Sentença desfavorável")
        self.assertEqual((p.provisao, self.saldo("2.1.4"), self.saldo("4.10")), (D("50000"), D("50000"), D("50000")))
        p.avaliar_risco("provavel", D("30000"))
        self.assertEqual((p.provisao, self.saldo("2.1.4")), (D("30000"), D("30000")))
        p.avaliar_risco("remoto", D("0"))
        self.assertEqual((p.provisao, self.saldo("2.1.4"), self.saldo("4.10")), (D("0"), D("0"), D("0")))
        self.assertEqual(p.reavaliacoes.count(), 4)
        self.assertGreaterEqual(p.andamentos.count(), 4)

    def test_valor_negativo_recusado(self):
        with self.assertRaises(ValidationError):
            self.processo().avaliar_risco("provavel", D("-1"))

    def test_encerrar_com_condenacao_reverte_provisao_e_gera_conta_a_pagar(self):
        p = self.processo()
        p.avaliar_risco("provavel", D("50000"))
        p.encerrar("parcial", valor_condenacao=D("32000"))
        p.refresh_from_db()
        self.assertEqual((p.status, p.provisao, p.risco, p.valor_condenacao), ("encerrado", 0, "remoto", D("32000")))
        self.assertEqual(self.saldo("2.1.4"), D("0"))
        cp = ContaPagar.objects.get()
        self.assertEqual((cp.valor, cp.documento), (D("32000"), p.numero))
        with self.assertRaises(ValidationError):
            p.encerrar("acordo")
        with self.assertRaises(ValidationError):
            p.avaliar_risco("provavel", D("1"))

    def test_encerrar_vencedor_sem_conta_a_pagar(self):
        p = self.processo()
        p.encerrar("improcedente")
        self.assertFalse(ContaPagar.objects.exists())

    def test_autor_nao_gera_conta_a_pagar(self):
        p = self.processo(polo="autor")
        p.encerrar("procedente", valor_condenacao=D("1000"))
        self.assertFalse(ContaPagar.objects.exists())

    def test_suspender_e_retomar(self):
        p = self.processo()
        p.suspender()
        with self.assertRaises(ValidationError):
            p.suspender()
        p.retomar()
        with self.assertRaises(ValidationError):
            p.retomar()
        self.assertEqual(p.status, "andamento")


class PrazoTest(Base):
    def prazo(self, p, dias, **kw):
        return Prazo.objects.create(processo=p, descricao="Contestação", data_fatal=self.hoje + datetime.timedelta(days=dias), **kw)

    def test_alerta_cria_atividade_uma_vez(self):
        p = self.processo(responsavel=self.adv)
        self.prazo(p, 3)
        self.prazo(p, 30)
        processar_vencimentos(self.hoje)
        processar_vencimentos(self.hoje)
        ativ = Atividade.objects.filter(etiquetas="processo, prazo")
        self.assertEqual(ativ.count(), 1)
        self.assertEqual(ativ.get().responsavel, self.adv)

    def test_prazo_vencido_alerta_e_urgente(self):
        p = self.processo()
        self.prazo(p, -2)
        processar_vencimentos(self.hoje)
        a = Atividade.objects.get(etiquetas="processo, prazo")
        self.assertEqual(a.prioridade, "urgente")
        self.assertIn("VENCEU", a.descricao)

    def test_processo_encerrado_ou_prazo_cumprido_nao_alertam(self):
        p = self.processo()
        pr = self.prazo(p, 1)
        pr.cumprir()
        with self.assertRaises(ValidationError):
            pr.cumprir()
        p2 = self.processo("2")
        self.prazo(p2, 1)
        p2.encerrar("acordo")
        processar_vencimentos(self.hoje)
        self.assertFalse(Atividade.objects.filter(etiquetas="processo, prazo").exists())

    def test_proximo_prazo(self):
        p = self.processo()
        self.prazo(p, 10)
        perto = self.prazo(p, 2)
        self.assertEqual(p.proximo_prazo, perto)


class TelasTest(Base):
    def setUp(self):
        self.client.force_login(self.adv)

    def test_fluxo_pelas_telas(self):
        r = self.client.post(reverse("juridico:processo_create"), {
            "numero": "99-1", "tipo": "judicial", "natureza": "civel", "assunto": "Cobrança indevida",
            "polo": "reu", "contraparte": "Cliente X", "data_distribuicao": self.hoje.isoformat(),
            "valor_causa": "10000"})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:500])
        p = Processo.objects.get(numero="99-1")
        self.assertEqual(self.client.get(p.get_absolute_url()).status_code, 200)
        r = self.client.post(reverse("juridico:processo_andamento", args=[p.pk]),
                             {"data": self.hoje.isoformat(), "tipo": "despacho", "descricao": "Citação expedida"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Andamento.objects.filter(processo=p, descricao="Citação expedida").exists())
        r = self.client.post(reverse("juridico:processo_prazo", args=[p.pk]), {
            "descricao": "Contestação", "data_fatal": (self.hoje + datetime.timedelta(days=15)).isoformat(),
            "alertar_dias": 5})
        self.assertEqual(r.status_code, 302)
        prazo = Prazo.objects.get(processo=p)
        self.client.post(reverse("juridico:processo_envolvido", args=[p.pk]), {"nome": "Dra. Ana", "papel": "advogado"})
        self.assertTrue(Envolvido.objects.filter(processo=p).exists())
        self.client.post(reverse("juridico:processo_risco", args=[p.pk]), {
            "risco": "provavel", "valor_estimado": "4000", "motivo": "x"})
        p.refresh_from_db()
        self.assertEqual(p.provisao, D("4000"))
        self.client.post(reverse("juridico:prazo_cumprir", args=[prazo.pk]))
        prazo.refresh_from_db()
        self.assertEqual(prazo.status, "cumprido")
        self.assertEqual(self.client.get(reverse("juridico:contencioso")).status_code, 200)
        self.assertEqual(self.client.get(reverse("juridico:prazo_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("juridico:processo_list")).status_code, 200)
        self.client.post(reverse("juridico:processo_encerrar", args=[p.pk]), {
            "resultado": "acordo", "data": self.hoje.isoformat(), "valor_condenacao": "2500"})
        p.refresh_from_db()
        self.assertEqual((p.status, p.provisao), ("encerrado", 0))
        self.assertEqual(ContaPagar.objects.get().valor, D("2500"))

    def test_sem_permissao(self):
        self.client.force_login(User.objects.create_user("x", password="x"))
        p = self.processo()
        self.assertEqual(self.client.get(reverse("juridico:contencioso")).status_code, 403)
        self.assertEqual(self.client.post(reverse("juridico:processo_risco", args=[p.pk]), {}).status_code, 403)
