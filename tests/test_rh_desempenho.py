import datetime
import io
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Colaborador, Empresa
from apps.rh.desempenho import (AcaoDesenvolvimento, Avaliacao, CicloAvaliacao, Competencia, ItemModelo,
                                ModeloAvaliacao, conceito)

HOJE = timezone.localdate()
CPFS = ["52998224725", "11144477735", "39053344705", "16899535009"]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181", regime_tributario="simples")
        cls.rh = User.objects.create_user("rh", password="x")
        cls.rh.groups.add(Group.objects.get(name="RH"))
        cls.ug = User.objects.create_user("gestor", password="x")
        cls.uc = User.objects.create_user("colab", password="x")
        cls.uo = User.objects.create_user("outro", password="x")
        ant = HOJE - datetime.timedelta(days=400)
        cls.gestor = Colaborador.objects.create(empresa=cls.emp, nome="Gestora", cpf=CPFS[0], salario=D("9000"),
                                                usuario=cls.ug, data_admissao=ant)
        cls.colab = Colaborador.objects.create(empresa=cls.emp, nome="Colab", cpf=CPFS[1], salario=D("3000"),
                                               usuario=cls.uc, gestor=cls.gestor, data_admissao=ant)
        cls.novo = Colaborador.objects.create(empresa=cls.emp, nome="Recém-chegado", cpf=CPFS[2], salario=D("3000"),
                                              gestor=cls.gestor, data_admissao=HOJE + datetime.timedelta(days=5))
        cls.c1 = Competencia.objects.create(nome="Entrega")
        cls.c2 = Competencia.objects.create(nome="Trabalho em equipe")
        cls.modelo = ModeloAvaliacao.objects.create(nome="Padrão", escala_max=5)
        cls.i1 = ItemModelo.objects.create(modelo=cls.modelo, competencia=cls.c1, peso=D("2"))
        cls.i2 = ItemModelo.objects.create(modelo=cls.modelo, competencia=cls.c2, peso=D("1"))

    def ciclo(self, **kw):
        return CicloAvaliacao.objects.create(nome="2026", modelo=self.modelo, empresa=self.emp, **kw)

    def aberto(self, **kw):
        c = self.ciclo(**kw)
        c.abrir()
        return c

    def notas(self, a, b):
        return {self.i1.pk: a, self.i2.pk: b}


class CicloTest(Base):
    def test_abrir_cria_avaliacoes_dos_elegiveis(self):
        c = self.ciclo()
        n = c.abrir()
        self.assertEqual(n, 2)   # a gestora e o colaborador; o recém-chegado fica de fora
        self.assertEqual(c.status, "aberto")
        a = c.avaliacoes.get(colaborador=self.colab)
        self.assertEqual(a.gestor, self.gestor)
        with self.assertRaises(ValidationError):
            c.abrir()

    def test_modelo_sem_itens_nao_abre(self):
        m = ModeloAvaliacao.objects.create(nome="Vazio")
        c = CicloAvaliacao.objects.create(nome="x", modelo=m, empresa=self.emp)
        with self.assertRaises(ValidationError):
            c.abrir()

    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            CicloAvaliacao(nome="x", modelo=self.modelo, data_inicio=HOJE, data_fim=HOJE - datetime.timedelta(1)).clean()
        with self.assertRaises(ValidationError):
            ItemModelo(modelo=self.modelo, competencia=self.c1, peso=0).clean()
        with self.assertRaises(ValidationError):
            ModeloAvaliacao(nome="y", escala_max=1).clean()

    def test_conceito(self):
        self.assertEqual(conceito(D("1.5"), 5), "Abaixo do esperado")
        self.assertEqual(conceito(D("3.5"), 5), "Atende")
        self.assertEqual(conceito(D("5"), 5), "Excepcional")
        self.assertEqual(conceito(None, 5), "")


class RespostaTest(Base):
    def test_notas_ponderadas_e_final(self):
        c = self.aberto(peso_gestor=70)
        a = c.avaliacoes.get(colaborador=self.colab)
        self.assertEqual(a.responder("auto", self.notas(5, 4), usuario=self.uc), D("4.67"))   # (5*2+4*1)/3
        self.assertEqual(a.status, "andamento")
        self.assertIsNone(a.nota_final)
        a.responder("gestor", self.notas(3, 3), "ok", usuario=self.ug)
        a.refresh_from_db()
        self.assertEqual(a.status, "concluida")
        self.assertEqual(a.nota_gestor, D("3.00"))
        self.assertEqual(a.nota_final, D("3.50"))   # 3*0,7 + 4,67*0,3 = 3,501
        self.assertEqual(a.respostas.count(), 4)

    def test_sem_autoavaliacao(self):
        c = self.aberto(inclui_auto=False)
        a = c.avaliacoes.get(colaborador=self.colab)
        with self.assertRaises(ValidationError):
            a.responder("auto", self.notas(3, 3))
        a.responder("gestor", self.notas(4, 2), usuario=self.ug)
        a.refresh_from_db()
        self.assertEqual(a.nota_final, D("3.33"))

    def test_regras(self):
        c = self.aberto()
        a = c.avaliacoes.get(colaborador=self.colab)
        with self.assertRaises(ValidationError):
            a.responder("auto", {self.i1.pk: 5})            # falta competência
        with self.assertRaises(ValidationError):
            a.responder("auto", self.notas(6, 3))           # fora da escala
        with self.assertRaises(ValidationError):
            a.responder("auto", self.notas(3, 3), usuario=self.ug)   # o gestor não responde pela autoavaliação
        with self.assertRaises(ValidationError):
            a.responder("gestor", self.notas(3, 3), usuario=self.uo)  # estranho não avalia
        a.responder("auto", self.notas(3, 3), usuario=self.uc)
        with self.assertRaises(ValidationError):
            a.responder("auto", self.notas(4, 4), usuario=self.uc)    # envio único

    def test_ciclo_fechado_nao_aceita(self):
        c = self.aberto()
        c.iniciar_calibracao()
        a = c.avaliacoes.get(colaborador=self.colab)
        with self.assertRaises(ValidationError):
            a.responder("auto", self.notas(3, 3))

    def test_sem_gestor_quem_avalia_e_o_rh(self):
        c = self.aberto()
        a = c.avaliacoes.get(colaborador=self.gestor)   # a gestora não tem gestor
        self.assertTrue(a.pode_responder(self.rh, "gestor"))
        self.assertFalse(a.pode_responder(self.uo, "gestor"))


class CalibracaoTest(Base):
    def concluida(self):
        c = self.aberto()
        a = c.avaliacoes.get(colaborador=self.colab)
        a.responder("auto", self.notas(5, 5), usuario=self.uc)
        a.responder("gestor", self.notas(3, 3), usuario=self.ug)
        return c, Avaliacao.objects.get(pk=a.pk)

    def test_calibrar_so_em_calibracao(self):
        c, a = self.concluida()
        with self.assertRaises(ValidationError):
            a.calibrar(4, "ajuste", self.rh)
        c.iniciar_calibracao()
        with self.assertRaises(ValidationError):
            a.calibrar(4, "  ", self.rh)
        with self.assertRaises(ValidationError):
            a.calibrar(9, "ajuste", self.rh)
        a.calibrar(4, "Entrega acima da média do time", self.rh)
        a.refresh_from_db()
        self.assertEqual(a.nota_calibrada, D("4.00"))
        self.assertEqual(a.nota_efetiva, D("4.00"))
        self.assertEqual(a.nota_final, D("3.60"))   # original preservada
        self.assertEqual(a.calibrada_por, self.rh)

    def test_encerrar_marca_incompletas(self):
        c, a = self.concluida()
        c.encerrar()
        self.assertEqual(c.status, "encerrado")
        outra = c.avaliacoes.get(colaborador=self.gestor)
        self.assertEqual(outra.status, "incompleta")
        self.assertIsNone(outra.nota_final)
        self.assertEqual(Avaliacao.objects.get(pk=a.pk).status, "concluida")
        with self.assertRaises(ValidationError):
            c.encerrar()


class PdiTest(Base):
    def test_acoes(self):
        c = self.aberto()
        a = c.avaliacoes.get(colaborador=self.colab)
        x = AcaoDesenvolvimento.objects.create(avaliacao=a, descricao="Curso de comunicação", competencia=self.c2)
        x.concluir()
        self.assertEqual(x.status, "concluida")
        self.assertEqual(x.concluida_em, HOJE)
        with self.assertRaises(ValidationError):
            x.cancelar()


class TelasTest(Base):
    def test_fluxo_pela_interface(self):
        self.client.force_login(self.rh)
        c = self.ciclo()
        r = self.client.post(reverse("rh:ciclo_abrir", args=[c.pk]))
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, "aberto")
        a = c.avaliacoes.get(colaborador=self.colab)

        self.client.force_login(self.uc)
        self.assertContains(self.client.get(reverse("rh:minhas_avaliacoes")), "Responder")
        self.assertEqual(self.client.get(reverse("rh:avaliacao_responder_auto", args=[a.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("rh:avaliacao_responder_gestor", args=[a.pk])).status_code, 403)
        self.client.post(reverse("rh:avaliacao_responder_auto", args=[a.pk]),
                         {f"nota_{self.i1.pk}": 4, f"nota_{self.i2.pk}": 4, "comentario": "bom"})
        a.refresh_from_db()
        self.assertEqual(a.nota_auto, D("4.00"))

        self.client.force_login(self.ug)
        self.assertContains(self.client.get(reverse("rh:minhas_avaliacoes")), "Colab")
        self.client.post(reverse("rh:avaliacao_responder_gestor", args=[a.pk]),
                         {f"nota_{self.i1.pk}": 5, f"nota_{self.i2.pk}": 5})
        a.refresh_from_db()
        self.assertEqual(a.status, "concluida")

        self.client.force_login(self.rh)
        self.client.post(reverse("rh:ciclo_calibrar", args=[c.pk]))          # abre a calibração
        c.refresh_from_db()
        self.assertEqual(c.status, "calibracao")
        self.assertEqual(self.client.get(reverse("rh:ciclo_calibrar", args=[c.pk])).status_code, 200)
        self.client.post(reverse("rh:ciclo_calibrar", args=[c.pk]),
                         {f"nota_{a.pk}": "4,5", f"motivo_{a.pk}": "Equalização"})
        a.refresh_from_db()
        self.assertEqual(a.nota_calibrada, D("4.50"))
        self.client.post(reverse("rh:avaliacao_pdi", args=[a.pk]), {"descricao": "Mentoria"})
        self.assertEqual(a.acoes.count(), 1)
        self.client.post(reverse("rh:ciclo_encerrar", args=[c.pk]))

        self.client.force_login(self.uc)
        self.assertContains(self.client.get(reverse("rh:minhas_avaliacoes")), "Mentoria")

    def test_sem_permissao(self):
        c = self.ciclo()
        self.client.force_login(self.uo)
        for nome in ("ciclo_abrir", "ciclo_calibrar", "ciclo_encerrar"):
            self.assertEqual(self.client.get(reverse(f"rh:{nome}", args=[c.pk])).status_code, 403)

    def test_listas_do_menu(self):
        self.client.force_login(self.rh)
        for nome in ("competencia", "modeloavaliacao", "itemmodelo", "cicloavaliacao", "avaliacao", "acaodesenvolvimento"):
            self.assertEqual(self.client.get(reverse(f"rh:{nome}_list")).status_code, 200, nome)
