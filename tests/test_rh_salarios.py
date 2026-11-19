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
from apps.rh import rotinas
from apps.rh.salarios import (Enquadramento, FaixaSalarial, MovimentoSalarial, aplicar_vigentes, aprovar_lote,
                              posicao_na_faixa, reajuste_coletivo)

HOJE = timezone.localdate()
FUT = HOJE + datetime.timedelta(days=20)
CPFS = ["52998224725", "11144477735", "39053344705", "16899535009"]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181", regime_tributario="simples")
        cls.jr = Cargo.objects.create(nome="Analista")
        cls.coord = Cargo.objects.create(nome="Coordenador")
        cls.f_jr = FaixaSalarial.objects.create(cargo=cls.jr, nivel="junior", minimo=D("3000"), maximo=D("4000"))
        cls.f_pl = FaixaSalarial.objects.create(cargo=cls.jr, nivel="pleno", minimo=D("4000"), maximo=D("6000"))
        cls.f_co = FaixaSalarial.objects.create(cargo=cls.coord, nivel="unico", minimo=D("7000"), maximo=D("9000"))
        cls.solic = User.objects.create_user("solic", password="x")
        cls.solic.groups.add(Group.objects.get(name="RH"))
        cls.aprov = User.objects.create_user("aprov", password="x")
        cls.aprov.groups.add(Group.objects.get(name="RH"))
        cls.outro = User.objects.create_user("outro", password="x")
        cls.c1 = Colaborador.objects.create(empresa=cls.emp, nome="Ana", cpf=CPFS[0], salario=D("3500"), cargo=cls.jr)
        cls.c2 = Colaborador.objects.create(empresa=cls.emp, nome="Bia", cpf=CPFS[1], salario=D("5000"), cargo=cls.jr)
        Enquadramento.objects.create(colaborador=cls.c1, faixa=cls.f_jr)

    def mov(self, colab=None, usuario=None, **kw):
        kw.setdefault("tipo", "merito")
        kw.setdefault("salario_novo", D("3800"))
        kw.setdefault("motivo", "Bom desempenho no ciclo")
        m = MovimentoSalarial(colaborador=colab or self.c1, **kw)
        m.full_clean()
        if usuario:
            m.criado_por = usuario
        m.save()
        return m


class FaixaTest(Base):
    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            FaixaSalarial(cargo=self.jr, nivel="senior", minimo=D("5"), maximo=D("3")).full_clean()
        with self.assertRaises(ValidationError):
            FaixaSalarial(cargo=self.jr, nivel="senior", minimo=0, maximo=D("3")).full_clean()
        self.assertEqual(self.f_jr.ponto_medio, D("3500.00"))

    def test_enquadramento_deve_seguir_o_cargo(self):
        with self.assertRaises(ValidationError):
            Enquadramento(colaborador=self.c2, faixa=self.f_co).full_clean()

    def test_compa_ratio_e_situacao(self):
        e = self.c1.enquadramento
        self.assertEqual(e.compa_ratio, D("1.00"))
        self.assertEqual(e.situacao, "dentro da faixa")
        Enquadramento.objects.create(colaborador=self.c2, faixa=self.f_jr)
        self.assertEqual(Enquadramento.objects.get(colaborador=self.c2).situacao, "acima da faixa")
        linhas = {l["colaborador"].nome: l for l in posicao_na_faixa()}
        self.assertEqual(linhas["Ana"]["situacao"], "dentro da faixa")
        self.assertEqual(linhas["Bia"]["situacao"], "acima da faixa")


class MovimentoTest(Base):
    def test_validacoes(self):
        with self.assertRaises(ValidationError):
            self.mov(salario_novo=D("3500"))                       # igual ao atual
        with self.assertRaises(ValidationError):
            self.mov(salario_novo=D("4500"), faixa_nova=self.f_jr)  # fora da faixa sem exceção
        with self.assertRaises(ValidationError):
            self.mov(tipo="promocao", salario_novo=D("7500"))      # promoção sem cargo/faixa
        with self.assertRaises(ValidationError):
            self.mov(salario_novo=D("7500"), faixa_nova=self.f_co)  # faixa de outro cargo
        with self.assertRaises(ValidationError):
            self.mov(salario_novo=D("4500"), faixa_nova=self.f_jr, excecao=True, motivo="curto")
        self.mov(salario_novo=D("4500"), faixa_nova=self.f_jr, excecao=True, motivo="Retenção de talento crítico")

    def test_aprovacao_aplica_na_hora(self):
        m = self.mov(usuario=self.solic)
        with self.assertRaises(ValidationError):
            m.aprovar(self.solic)                                  # segregação de funções
        m.aprovar(self.aprov, "ok")
        m.refresh_from_db()
        self.c1.refresh_from_db()
        self.assertEqual(m.status, "aplicado")
        self.assertEqual(self.c1.salario, D("3800.00"))
        self.assertEqual(m.salario_anterior, D("3500.00"))
        self.assertEqual(m.variacao_pct, D("8.57"))

    def test_superuser_pode_aprovar_a_propria(self):
        su = User.objects.create_superuser("root", password="x")
        m = self.mov(usuario=su)
        m.aprovar(su)
        self.assertEqual(m.status, "aplicado")

    def test_vigencia_futura_e_rotina(self):
        m = self.mov(usuario=self.solic, data_efetiva=FUT)
        m.aprovar(self.aprov)
        self.assertEqual(m.status, "aprovado")
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.salario, D("3500.00"))
        self.assertEqual(aplicar_vigentes(HOJE), 0)
        self.assertEqual(aplicar_vigentes(FUT), 1)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.salario, D("3800.00"))

    def test_rotina_diaria_inclui_salarios(self):
        self.mov(usuario=self.solic, data_efetiva=FUT).aprovar(self.aprov)
        r = rotinas.executar(FUT)
        self.assertEqual(r["salarios"], 1)

    def test_promocao_troca_cargo_e_enquadra(self):
        m = self.mov(usuario=self.solic, tipo="promocao", salario_novo=D("7500"), cargo_novo=self.coord,
                     faixa_nova=self.f_co, motivo="Promovida a coordenadora")
        m.aprovar(self.aprov)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.cargo, self.coord)
        self.assertEqual(self.c1.enquadramento.faixa, self.f_co)
        self.assertEqual(m.cargo_anterior, self.jr)

    def test_promocao_sem_faixa_limpa_enquadramento(self):
        m = self.mov(usuario=self.solic, tipo="promocao", salario_novo=D("7500"), cargo_novo=self.coord)
        m.aprovar(self.aprov)
        self.assertFalse(Enquadramento.objects.filter(colaborador=self.c1).exists())

    def test_rejeitar_e_cancelar(self):
        m = self.mov(usuario=self.solic)
        with self.assertRaises(ValidationError):
            m.rejeitar(self.aprov, " ")
        m.rejeitar(self.aprov, "Fora do orçamento")
        self.assertEqual(m.status, "rejeitado")
        with self.assertRaises(ValidationError):
            m.aprovar(self.aprov)
        m2 = self.mov(usuario=self.solic)
        m2.cancelar()
        self.assertEqual(m2.status, "cancelado")

    def test_desligado_antes_da_vigencia(self):
        m = self.mov(usuario=self.solic, data_efetiva=FUT)
        m.aprovar(self.aprov)
        self.c1.status = "desligado"
        self.c1.save()
        aplicar_vigentes(FUT)
        m.refresh_from_db()
        self.assertEqual(m.status, "cancelado")
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.salario, D("3500.00"))


class ReajusteTest(Base):
    def test_lote_e_aprovacao(self):
        lote, n = reajuste_coletivo(D("5"), HOJE, "Convenção coletiva 2026", empresa=self.emp, usuario=self.solic)
        self.assertEqual(n, 2)
        self.assertEqual(MovimentoSalarial.objects.filter(lote=lote, status="pendente").count(), 2)
        self.assertEqual(aprovar_lote(lote, self.solic), (0, 2))   # quem criou não aprova
        self.assertEqual(aprovar_lote(lote, self.aprov), (2, 0))
        self.c1.refresh_from_db()
        self.c2.refresh_from_db()
        self.assertEqual((self.c1.salario, self.c2.salario), (D("3675.00"), D("5250.00")))

    def test_recortes_e_erros(self):
        lote, n = reajuste_coletivo(D("10"), HOJE, "Política", cargo=self.coord) if False else (None, 0)
        with self.assertRaises(ValidationError):
            reajuste_coletivo(D("10"), HOJE, "Política", cargo=self.coord)   # ninguém nesse cargo
        with self.assertRaises(ValidationError):
            reajuste_coletivo(D("0"), HOJE, "x")
        with self.assertRaises(ValidationError):
            reajuste_coletivo(D("5"), HOJE, " ")
        lote, n = reajuste_coletivo(D("-5"), HOJE, "Redução acordada", cargo=self.jr)
        self.assertEqual(n, 2)


class TelasTest(Base):
    def test_fluxo(self):
        self.client.force_login(self.solic)
        r = self.client.post(reverse("rh:movimentosalarial_create"), {
            "colaborador": self.c1.pk, "tipo": "merito", "data_efetiva": HOJE.isoformat(), "salario_novo": "3700",
            "motivo": "Mérito anual"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        m = MovimentoSalarial.objects.get()
        self.assertEqual(m.criado_por, self.solic)
        self.client.post(reverse("rh:movimento_aprovar", args=[m.pk]), {"parecer": "ok"})   # solicitante não aprova
        m.refresh_from_db()
        self.assertEqual(m.status, "pendente")
        self.client.force_login(self.aprov)
        self.client.post(reverse("rh:movimento_aprovar", args=[m.pk]), {"parecer": "ok"})
        m.refresh_from_db()
        self.assertEqual(m.status, "aplicado")

    def test_reajuste_pela_tela(self):
        self.client.force_login(self.solic)
        self.client.post(reverse("rh:reajuste_coletivo"), {"percentual": "4,5", "data_efetiva": HOJE.isoformat(),
                                                           "motivo": "CCT"})
        self.assertEqual(MovimentoSalarial.objects.filter(tipo="dissidio").count(), 2)
        lote = MovimentoSalarial.objects.first().lote
        self.client.force_login(self.aprov)
        self.client.post(reverse("rh:aprovar_lote"), {"lote": lote, "parecer": ""})
        self.assertEqual(MovimentoSalarial.objects.filter(status="aplicado").count(), 2)

    def test_permissoes_e_telas(self):
        self.client.force_login(self.outro)
        for nome in ("reajuste_coletivo", "aprovar_lote", "posicao_faixa"):
            self.assertEqual(self.client.get(reverse(f"rh:{nome}")).status_code, 403, nome)
        self.client.force_login(self.solic)
        self.assertContains(self.client.get(reverse("rh:posicao_faixa")), "Ana")
        for nome in ("faixasalarial", "enquadramento", "movimentosalarial"):
            self.assertEqual(self.client.get(reverse(f"rh:{nome}_list")).status_code, 200, nome)
