import datetime
import io
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Cargo, Colaborador, Empresa
from apps.rh.recrutamento import Candidato, Candidatura, Entrevista, Vaga

HOJE = timezone.localdate()
FUT = HOJE + datetime.timedelta(days=15)
CPFS = ["52998224725", "11144477735", "39053344705", "16899535009"]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181", regime_tributario="simples")
        cls.cargo = Cargo.objects.create(nome="Analista")
        cls.rh = User.objects.create_user("rh", password="x")
        cls.rh.groups.add(Group.objects.get(name="RH"))
        cls.outro = User.objects.create_user("outro", password="x")
        cls.gestor = Colaborador.objects.create(empresa=cls.emp, nome="Gestor", cpf=CPFS[3], salario=D("9000"))

    def vaga(self, aberta=True, **kw):
        kw.setdefault("faixa_min", D("3000"))
        kw.setdefault("faixa_max", D("5000"))
        v = Vaga.objects.create(titulo="Analista Pl", cargo=self.cargo, empresa=self.emp, gestor=self.gestor, **kw)
        if aberta:
            v.abrir()
        return v

    def cand(self, i=0, lgpd=True, cpf=True, nome=None):
        return Candidato.objects.create(nome=nome or f"Candidato {i}", cpf=CPFS[i] if cpf else "", email=f"c{i}@x.com",
                                        consentimento_lgpd=lgpd)

    def candidatura(self, v, i=0, **kw):
        return Candidatura.objects.create(vaga=v, candidato=self.cand(i, **kw))

    def aceita(self, v, i=0, **kw):
        c = self.candidatura(v, i, **kw)
        c.mover("entrevista_rh")
        c.enviar_proposta(D("4000"), FUT)
        c.responder_proposta(True)
        return c


class VagaTest(Base):
    def test_ciclo_e_numeracao(self):
        v = self.vaga(aberta=False)
        self.assertTrue(v.numero.startswith(f"VAG-{HOJE.year}-"))
        v.abrir()
        self.assertEqual(v.aberta_em, HOJE)
        v.pausar()
        v.abrir()
        with self.assertRaises(ValidationError):
            v.abrir()
        v.cancelar()
        self.assertEqual(v.status, "cancelada")

    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            Vaga(titulo="x", cargo=self.cargo, quantidade=0).clean()
        with self.assertRaises(ValidationError):
            Vaga(titulo="x", cargo=self.cargo, faixa_min=D("5"), faixa_max=D("3")).clean()
        with self.assertRaises(ValidationError):
            Candidato(nome="x").clean()   # sem contato

    def test_cancelar_reprova_em_andamento(self):
        v = self.vaga()
        c = self.candidatura(v)
        v.cancelar()
        c.refresh_from_db()
        self.assertEqual(c.etapa, "reprovado")
        self.assertEqual(c.motivo, "Vaga cancelada")


class CandidaturaTest(Base):
    def test_exige_lgpd_e_vaga_valida(self):
        v = self.vaga()
        with self.assertRaises(ValidationError):
            Candidatura(vaga=v, candidato=self.cand(0, lgpd=False)).clean()
        v.cancelar()
        with self.assertRaises(ValidationError):
            Candidatura(vaga=v, candidato=self.cand(1)).clean()

    def test_funil_e_historico(self):
        v = self.vaga()
        c = self.candidatura(v)
        c.mover("entrevista_rh", self.rh, "bom perfil")
        c.mover("teste")
        c.mover("entrevista_gestor")   # pode voltar no funil antes da proposta
        self.assertEqual(c.historico.count(), 3)
        self.assertEqual(c.historico.first().usuario, self.rh)
        with self.assertRaises(ValidationError):
            c.mover("proposta")
        with self.assertRaises(ValidationError):
            c.mover("contratado")

    def test_reprovar_exige_motivo_e_encerra(self):
        c = self.candidatura(self.vaga())
        with self.assertRaises(ValidationError):
            c.reprovar(" ")
        c.reprovar("Sem a experiência exigida")
        self.assertEqual(c.etapa, "reprovado")
        with self.assertRaises(ValidationError):
            c.mover("teste")

    def test_vaga_pausada_nao_movimenta(self):
        v = self.vaga()
        c = self.candidatura(v)
        v.pausar()
        with self.assertRaises(ValidationError):
            c.mover("teste")


class PropostaContratacaoTest(Base):
    def test_proposta_validacoes(self):
        c = self.candidatura(self.vaga())
        with self.assertRaises(ValidationError):
            c.enviar_proposta(0, FUT)
        with self.assertRaises(ValidationError):
            c.enviar_proposta(D("9000"), FUT)    # acima da faixa
        with self.assertRaises(ValidationError):
            c.enviar_proposta(D("4000"), HOJE - datetime.timedelta(days=1))
        c.enviar_proposta(D("4000"), FUT)
        self.assertEqual((c.etapa, c.proposta_status), ("proposta", "enviada"))
        with self.assertRaises(ValidationError):
            c.mover("triagem")   # com proposta só reprova, desiste ou contrata

    def test_contratar_exige_aceite(self):
        c = self.candidatura(self.vaga())
        c.enviar_proposta(D("4000"), FUT)
        with self.assertRaises(ValidationError):
            c.contratar()

    def test_contratar_cria_colaborador_e_fecha_vaga(self):
        v = self.vaga(departamento=None)
        c = self.aceita(v)
        outro = self.candidatura(v, 1)
        colab = c.contratar(usuario=self.rh)
        c.refresh_from_db()
        self.assertEqual(c.etapa, "contratado")
        self.assertEqual(colab.cargo, self.cargo)
        self.assertEqual(colab.gestor, self.gestor)
        self.assertEqual(colab.salario, D("4000.00"))
        self.assertEqual(colab.data_admissao, FUT)
        self.assertEqual(colab.cpf, CPFS[0])
        v.refresh_from_db()
        self.assertEqual(v.status, "preenchida")
        outro.refresh_from_db()
        self.assertEqual((outro.etapa, outro.motivo), ("reprovado", "Vaga preenchida"))
        with self.assertRaises(ValidationError):
            c.contratar()

    def test_varias_posicoes(self):
        v = self.vaga(quantidade=2)
        self.aceita(v, 0).contratar()
        v.refresh_from_db()
        self.assertEqual((v.status, v.posicoes_livres), ("aberta", 1))
        self.aceita(v, 1).contratar()
        v.refresh_from_db()
        self.assertEqual(v.status, "preenchida")

    def test_cpf_ausente_e_duplicado(self):
        v = self.vaga()
        c = self.aceita(v, 0, cpf=False)
        with self.assertRaises(ValidationError):
            c.contratar()                       # sem CPF
        with self.assertRaises(ValidationError):
            c.contratar("123")                  # CPF inválido
        with self.assertRaises(ValidationError):
            c.contratar(CPFS[3])                # já é colaborador
        c.contratar("390.533.447-05")
        self.assertEqual(Colaborador.objects.get(nome="Candidato 0").cpf, CPFS[2])

    def test_recusa_encerra_como_desistencia(self):
        c = self.candidatura(self.vaga())
        c.enviar_proposta(D("4000"), FUT)
        c.responder_proposta(False)
        self.assertEqual((c.etapa, c.proposta_status), ("desistiu", "recusada"))

    def test_entrevista(self):
        c = self.candidatura(self.vaga())
        with self.assertRaises(ValidationError):
            Entrevista(candidatura=c, nota=7).clean()
        Entrevista(candidatura=c, nota=4).clean()


class TelasTest(Base):
    def test_fluxo_completo(self):
        self.client.force_login(self.rh)
        v = self.vaga(aberta=False)
        self.client.post(reverse("rh:vaga_abrir", args=[v.pk]))
        v.refresh_from_db()
        self.assertEqual(v.status, "aberta")
        c = self.candidatura(v)
        self.client.post(reverse("rh:candidatura_mover", args=[c.pk]), {"etapa": "teste", "observacao": ""})
        self.client.post(reverse("rh:candidatura_proposta", args=[c.pk]), {"salario": "4200", "inicio": FUT.isoformat()})
        self.client.post(reverse("rh:candidatura_aceite", args=[c.pk]))
        self.assertContains(self.client.get(reverse("rh:vaga_funil", args=[v.pk])), "Candidato 0")
        self.client.post(reverse("rh:candidatura_contratar", args=[c.pk]), {"cpf": ""})
        c.refresh_from_db()
        self.assertEqual(c.etapa, "contratado")
        self.assertEqual(c.colaborador.salario, D("4200.00"))

    def test_sem_permissao(self):
        v = self.vaga()
        c = self.candidatura(v)
        self.client.force_login(self.outro)
        self.assertEqual(self.client.get(reverse("rh:vaga_abrir", args=[v.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("rh:candidatura_contratar", args=[c.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("rh:vaga_funil", args=[v.pk])).status_code, 403)

    def test_listas_do_menu(self):
        self.client.force_login(self.rh)
        for nome in ("vaga", "candidato", "candidatura", "entrevista"):
            self.assertEqual(self.client.get(reverse(f"rh:{nome}_list")).status_code, 200, nome)
