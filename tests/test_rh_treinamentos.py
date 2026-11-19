import datetime
import io
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cargo, Colaborador, Empresa, Notificacao
from apps.rh import rotinas
from apps.rh.treinamentos import Curso, Participacao, Turma, alertar_reciclagem, pendencias, somar_meses

HOJE = timezone.localdate()
CPFS = ["52998224725", "11144477735", "39053344705", "16899535009", "74352693018", "12345678909"]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181", regime_tributario="simples")
        cls.cargo = Cargo.objects.create(nome="Operador")
        cls.rh = User.objects.create_user("rh", password="x")
        cls.rh.groups.add(Group.objects.get(name="RH"))
        cls.curso = Curso.objects.create(nome="NR-12", categoria="seguranca", carga_horaria=8, validade_meses=24,
                                         frequencia_minima=75, nota_minima=D("7"))
        cls.curso.cargos_obrigatorios.add(cls.cargo)

    def colab(self, i, **kw):
        kw.setdefault("cargo", self.cargo)
        return Colaborador.objects.create(empresa=self.emp, nome=f"Colab {i}", cpf=CPFS[i], salario=D("3000"), **kw)

    def turma(self, **kw):
        kw.setdefault("data_inicio", HOJE - datetime.timedelta(days=10))
        kw.setdefault("data_fim", HOJE - datetime.timedelta(days=9))
        return Turma.objects.create(curso=self.curso, empresa=self.emp, **kw)

    def realizar(self, colab, freq=100, nota="9", **kw):
        t = self.turma(**kw)
        t.inscrever([colab])
        t.iniciar()
        p = t.participacoes.get()
        p.registrar(freq, D(nota))
        t.concluir()
        p.refresh_from_db()
        return t, p


class TurmaTest(Base):
    def test_numeracao_e_ciclo(self):
        t = self.turma()
        self.assertTrue(t.numero.startswith(f"TRM-{HOJE.year}-"))
        with self.assertRaises(ValidationError):
            t.iniciar()   # sem inscritos
        t.inscrever([self.colab(0), self.colab(1)])
        self.assertEqual(t.inscritos, 2)
        t.iniciar()
        with self.assertRaises(ValidationError):
            t.inscrever([self.colab(2)])   # já iniciada
        t.concluir()
        self.assertEqual(t.status, "concluida")
        with self.assertRaises(ValidationError):
            t.cancelar()

    def test_vagas_duplicidade_e_desligado(self):
        t = self.turma(vagas=2)
        a, b, c = self.colab(0), self.colab(1), self.colab(2)
        t.inscrever([a])
        with self.assertRaises(ValidationError):
            t.inscrever([a])
        with self.assertRaises(ValidationError):
            t.inscrever([b, c])   # só resta 1 vaga
        self.assertEqual(t.vagas_livres, 1)
        d = self.colab(3, status="desligado")
        with self.assertRaises(ValidationError):
            t.inscrever([d])

    def test_cancelar_e_reinscrever(self):
        t = self.turma()
        a = self.colab(0)
        t.inscrever([a])
        Participacao.objects.filter(turma=t).update(resultado="cancelado")
        self.assertEqual(t.inscrever([a]), 1)
        self.assertEqual(Participacao.objects.filter(turma=t).count(), 1)
        t.cancelar()
        self.assertEqual(t.status, "cancelada")
        self.assertEqual(t.inscritos, 0)

    def test_custo_por_participante(self):
        t = self.turma(custo_total=D("900"))
        t.inscrever([self.colab(0), self.colab(1), self.colab(2)])
        self.assertEqual(t.custo_por_participante, D("300.00"))

    def test_fim_antes_do_inicio(self):
        with self.assertRaises(ValidationError):
            Turma(curso=self.curso, data_inicio=HOJE, data_fim=HOJE - datetime.timedelta(days=1)).full_clean(
                exclude=["empresa"])


class ResultadoTest(Base):
    def test_aprovado_tem_validade(self):
        t, p = self.realizar(self.colab(0))
        self.assertEqual(p.resultado, "aprovado")
        self.assertEqual(p.concluido_em, t.data_fim)
        self.assertEqual(p.valido_ate, somar_meses(t.data_fim, 24))

    def test_reprovado_por_nota_e_por_frequencia_e_ausente(self):
        self.assertEqual(self.realizar(self.colab(0), nota="6")[1].resultado, "reprovado")
        self.assertEqual(self.realizar(self.colab(1), freq=60)[1].resultado, "reprovado")
        t = self.turma()
        t.inscrever([self.colab(2)])
        t.iniciar()
        t.concluir()
        self.assertEqual(t.participacoes.get().resultado, "ausente")

    def test_sem_nota_minima_basta_frequencia(self):
        curso = Curso.objects.create(nome="Integração", frequencia_minima=75)
        t = Turma.objects.create(curso=curso, empresa=self.emp, data_inicio=HOJE, data_fim=HOJE)
        t.inscrever([self.colab(0)])
        t.iniciar()
        t.participacoes.get().registrar(80, None)
        t.concluir()
        p = t.participacoes.get()
        self.assertEqual((p.resultado, p.valido_ate), ("aprovado", None))

    def test_registrar_exige_turma_em_andamento_e_valores_validos(self):
        t = self.turma()
        t.inscrever([self.colab(0)])
        p = t.participacoes.get()
        with self.assertRaises(ValidationError):
            p.registrar(100, D("9"))   # ainda planejada
        t.iniciar()
        with self.assertRaises(ValidationError):
            p.registrar(101, D("9"))
        with self.assertRaises(ValidationError):
            p.registrar(90, D("-1"))


class PendenciaTest(Base):
    def test_situacoes(self):
        nunca, ok, vencido, a_vencer = self.colab(0), self.colab(1), self.colab(2), self.colab(3)
        outro_cargo = self.colab(4, cargo=Cargo.objects.create(nome="Analista"))
        desligado = self.colab(5, status="desligado")
        self.realizar(ok, data_inicio=HOJE - datetime.timedelta(days=5), data_fim=HOJE - datetime.timedelta(days=4))
        self.realizar(vencido, data_inicio=HOJE - datetime.timedelta(days=1000),
                      data_fim=HOJE - datetime.timedelta(days=999))
        self.realizar(a_vencer, data_inicio=somar_meses(HOJE, -24) + datetime.timedelta(days=20),
                      data_fim=somar_meses(HOJE, -24) + datetime.timedelta(days=21))
        por = {p["colaborador"].pk: p["situacao"] for p in pendencias(HOJE)}
        self.assertEqual(por, {nunca.pk: "pendente", vencido.pk: "vencido", a_vencer.pk: "a_vencer"})
        self.assertNotIn(outro_cargo.pk, por)
        self.assertNotIn(desligado.pk, por)

    def test_reciclagem_renova_a_validade(self):
        c = self.colab(0)
        self.realizar(c, data_inicio=HOJE - datetime.timedelta(days=1000), data_fim=HOJE - datetime.timedelta(days=999))
        self.assertEqual(pendencias(HOJE)[0]["situacao"], "vencido")
        self.realizar(c)
        self.assertEqual(pendencias(HOJE), [])

    def test_alerta_diario_sem_repetir_fora_da_janela(self):
        usuario = User.objects.create_user("colab", password="x")
        c = self.colab(0, usuario=usuario)
        base = somar_meses(HOJE, -24)
        self.realizar(c, data_inicio=base + datetime.timedelta(days=14), data_fim=base + datetime.timedelta(days=15))
        # validade = HOJE + 15 dias
        self.assertEqual(alertar_reciclagem(HOJE), 1)
        self.assertEqual(Notificacao.objects.filter(usuario=usuario).count(), 1)
        self.assertEqual(alertar_reciclagem(HOJE + datetime.timedelta(days=1)), 0)   # faltam 14 dias
        self.assertEqual(rotinas.executar(HOJE)["treinamentos"], 1)


class TelasTest(Base):
    def setUp(self):
        self.client.force_login(self.rh)

    def test_fluxo_pelas_telas(self):
        r = self.client.post(reverse("rh:curso_create"), {
            "nome": "LGPD", "categoria": "compliance", "modalidade": "online", "carga_horaria": "4",
            "validade_meses": "12", "frequencia_minima": "75", "nota_minima": "0", "ativo": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        curso = Curso.objects.get(nome="LGPD")
        r = self.client.post(reverse("rh:turma_create"), {
            "curso": curso.pk, "data_inicio": HOJE.isoformat(), "data_fim": HOJE.isoformat(), "vagas": "5",
            "custo_total": "500", "empresa": self.emp.pk})
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        t = Turma.objects.get(curso=curso)
        self.assertEqual(self.client.get(t.get_absolute_url()).status_code, 200)
        a, b = self.colab(0), self.colab(1)
        r = self.client.post(reverse("rh:turma_inscrever", args=[t.pk]), {"colaboradores": [a.pk, b.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(t.inscritos, 2)
        self.assertEqual(self.client.post(reverse("rh:turma_iniciar", args=[t.pk])).status_code, 302)
        pa = t.participacoes.get(colaborador=a)
        pb = t.participacoes.get(colaborador=b)
        self.assertEqual(self.client.get(reverse("rh:turma_resultados", args=[t.pk])).status_code, 200)
        r = self.client.post(reverse("rh:turma_resultados", args=[t.pk]), {
            f"freq_{pa.pk}": "100", f"nota_{pa.pk}": "9,5", f"freq_{pb.pk}": "150"})
        self.assertEqual(r.status_code, 200)   # frequência inválida do segundo: volta com erro
        pa.refresh_from_db()
        self.assertEqual((pa.presenca_pct, pa.nota), (100, D("9.50")))
        self.client.post(reverse("rh:turma_resultados", args=[t.pk]), {f"freq_{pb.pk}": "50"})
        self.client.post(reverse("rh:turma_concluir", args=[t.pk]))
        pa.refresh_from_db(); pb.refresh_from_db()
        self.assertEqual((pa.resultado, pb.resultado), ("aprovado", "reprovado"))
        self.assertEqual(self.client.get(reverse("rh:pendencias_treinamento")).status_code, 200)
        self.assertEqual(self.client.get(reverse("rh:participacao_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("rh:turma_list")).status_code, 200)

    def test_meus_treinamentos(self):
        usuario = User.objects.create_user("meu", password="x")
        c = self.colab(0, usuario=usuario)
        self.client.force_login(usuario)
        r = self.client.get(reverse("rh:meus_treinamentos"))
        self.assertContains(r, "NR-12")   # obrigatório ainda não feito
        self.realizar(c)
        self.assertContains(self.client.get(reverse("rh:meus_treinamentos")), "Aprovado")

    def test_sem_permissao(self):
        self.client.force_login(User.objects.create_user("x", password="x"))
        t = self.turma()
        for nome in ("turma_inscrever", "turma_iniciar", "turma_resultados", "turma_concluir", "turma_cancelar"):
            self.assertEqual(self.client.get(reverse(f"rh:{nome}", args=[t.pk])).status_code, 403, nome)
        self.assertEqual(self.client.get(reverse("rh:pendencias_treinamento")).status_code, 403)
