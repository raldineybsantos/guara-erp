import datetime
import io
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Colaborador, Empresa
from apps.financeiro.models import ContaPagar
from apps.rh import calculo, ferias, folha as folha_svc, ponto
from apps.rh.models import (AbonoPonto, DadosTrabalhistas, Dependente, Feriado, Folha, Jornada, LancamentoFolha,
                            Marcacao, ParametroFolha, PeriodoAquisitivo, Rescisao, Rubrica, SolicitacaoFerias)


def data(s):
    return datetime.date.fromisoformat(s)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "SenhaForte#2026")
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181", regime_tributario="simples")
        cls.p = ParametroFolha.vigente(data("2026-06-01"))
        cls.fi, cls.fr = cls.p.faixas_inss(), cls.p.faixas_irrf()
        cls.jornada = Jornada.objects.first()

    def colab(self, salario=3000, nome="Fulano", cpf="52998224725", adm="2025-01-01", **kw):
        c = Colaborador.objects.create(empresa=self.emp, nome=nome, cpf=cpf, salario=D(salario),
                                       data_admissao=data(adm), matricula=nome[:3], **kw)
        DadosTrabalhistas.objects.create(colaborador=c, jornada=self.jornada)
        return c


class CalculoTest(Base):
    def test_inss_progressivo_e_teto(self):
        self.assertEqual(calculo.inss(1000, self.fi), D("75.00"))
        self.assertEqual(calculo.inss(3000, self.fi), D("248.60"))
        self.assertEqual(calculo.inss(8475.55, self.fi), D("988.09"))
        self.assertEqual(calculo.inss(50000, self.fi), D("988.09"))   # acima do teto

    def test_irrf_isento_ate_5_mil_pela_reducao(self):
        for sal, esperado in ((3000, "0.00"), (5000, "0.00")):
            i = calculo.inss(sal, self.fi)
            self.assertEqual(calculo.irrf(sal, i, 0, self.p, self.fr)[0], D(esperado), sal)

    def test_irrf_faixa_de_reducao_decrescente_e_sem_reducao(self):
        i = calculo.inss(6000, self.fi)
        self.assertEqual(i, D("641.51"))
        # bruto 564,85 menos redução 978,62 − 0,133145 × 6000 = 179,75
        self.assertEqual(calculo.irrf(6000, i, 0, self.p, self.fr)[0], D("385.10"))
        i = calculo.inss(12000, self.fi)
        self.assertEqual(calculo.irrf(12000, i, 0, self.p, self.fr)[0], D("2119.55"))   # sem redução acima de 7.350

    def test_irrf_escolhe_a_deducao_mais_vantajosa_e_dependentes(self):
        i = calculo.inss(6000, self.fi)
        com = calculo.irrf(6000, i, 3, self.p, self.fr)[0]
        sem = calculo.irrf(6000, i, 0, self.p, self.fr)[0]
        self.assertLess(com, sem)

    def test_salario_familia_e_limite(self):
        self.assertEqual(calculo.salario_familia(1800, 2, self.p), D("135.08"))
        self.assertEqual(calculo.salario_familia(2000, 2, self.p), D("0"))

    def test_ferias_aviso_e_avos(self):
        self.assertEqual(calculo.ferias(3000, 30, 10), {"ferias": D("3000.00"), "terco": D("1000.00"),
                                                         "abono": D("1000.00"), "terco_abono": D("333.33")})
        self.assertEqual([calculo.direito_ferias(n) for n in (0, 5, 6, 15, 24, 33)], [30, 30, 24, 18, 12, 0])
        self.assertEqual([calculo.dias_aviso(n) for n in (0, 2, 20)], [30, 36, 90])
        self.assertEqual(calculo.meses_avos(data("2026-01-01"), data("2026-04-20")), 4)
        self.assertEqual(calculo.meses_avos(data("2026-03-20"), data("2026-03-31")), 0)   # menos de 15 dias


class FolhaTest(Base):
    def folha(self, comp="2026-03-01", tipo="mensal"):
        return Folha.objects.get_or_create(empresa=self.emp, competencia=data(comp), tipo=tipo)[0]

    def test_folha_mensal_simples(self):
        c = self.colab(3000)
        f = folha_svc.calcular(self.folha())
        h = f.holerites.get(colaborador=c)
        self.assertEqual((h.bruto, h.inss, h.irrf, h.fgts, h.liquido), (D("3000"), D("248.60"), D("0"), D("240.00"),
                                                                       D("2751.40")))
        self.assertEqual(h.patronal, 0)             # Simples: INSS patronal está no DAS
        self.assertEqual(f.total_liquido, D("2751.40"))
        self.assertEqual(f.status, "calculada")

    def test_patronal_no_lucro_presumido(self):
        self.emp.regime_tributario = "presumido"
        self.emp.save()
        self.colab(3000)
        f = folha_svc.calcular(self.folha())
        self.assertEqual(f.total_patronal, D("834.00"))   # 3000 × (20 + 2 + 5,8)%

    def test_salario_familia_dependente_e_proporcional_na_admissao(self):
        c = self.colab(1800)
        Dependente.objects.create(colaborador=c, nome="Filha", data_nascimento=data("2020-05-01"))
        h = folha_svc.calcular(self.folha()).holerites.get()
        self.assertEqual(h.itens.get(codigo="0020").valor, D("67.54"))
        self.assertEqual(h.bruto, D("1867.54"))
        novo = self.colab(3000, nome="Beltrano", cpf="11144477735", adm="2026-03-16")
        h2 = folha_svc.calcular(self.folha()).holerites.get(colaborador=novo)
        self.assertEqual(h2.itens.get(codigo="0001").valor, D("1600.00"))   # 16 dias de 30

    def test_lancamentos_faltas_e_horas_extras(self):
        c = self.colab(3000)
        LancamentoFolha.objects.create(colaborador=c, competencia=data("2026-03-01"),
                                       rubrica=Rubrica.objects.get(codigo="0130"), valor=D("500"))
        LancamentoFolha.objects.create(colaborador=c, competencia=data("2026-03-01"),
                                       rubrica=Rubrica.objects.get(codigo="0200"), valor=D("100"))
        h = folha_svc.calcular(self.folha()).holerites.get()
        self.assertEqual(h.base_inss, D("3400.00"))   # salário + comissão − falta (todos incidem)

    def test_estagiario_sem_encargos_e_pj_fora_da_folha(self):
        self.colab(1500, nome="Estag", cpf="11144477735", tipo_contrato="estagio")
        self.colab(9000, nome="Pj", cpf="39053344705", tipo_contrato="pj")
        f = folha_svc.calcular(self.folha())
        self.assertEqual(f.holerites.count(), 1)
        h = f.holerites.get()
        self.assertEqual((h.inss, h.irrf, h.fgts, h.liquido), (0, 0, 0, D("1500.00")))

    def test_ferias_entram_na_folha_e_reduzem_o_salario(self):
        c = self.colab(3000)
        ferias.gerar_periodos(c, ate=data("2026-03-01"))
        per = c.periodos_ferias.get(inicio=data("2025-01-01"))
        SolicitacaoFerias.objects.create(colaborador=c, periodo=per, inicio=data("2026-03-10"), dias=30)
        SolicitacaoFerias.objects.filter(colaborador=c).update(status="aprovada")
        h = folha_svc.calcular(self.folha("2026-03-01")).holerites.get()
        self.assertEqual(h.itens.get(codigo="0010").valor, D("3000.00"))
        self.assertEqual(h.itens.get(codigo="0011").valor, D("1000.00"))
        self.assertEqual(h.itens.get(codigo="0001").valor, D("800.00"))   # 30 − 22 dias de férias no mês

    def test_decimo_terceiro_duas_parcelas(self):
        c = self.colab(3000)
        f1 = folha_svc.calcular(self.folha("2026-11-01", "13_1"))
        self.assertEqual(f1.holerites.get().liquido, D("1500.00"))
        f2 = folha_svc.calcular(self.folha("2026-12-01", "13_2"))
        h = f2.holerites.get(colaborador=c)
        self.assertEqual((h.bruto, h.inss, h.irrf, h.liquido), (D("3000.00"), D("248.60"), D("0"), D("1251.40")))

    def test_fechar_gera_contas_a_pagar_e_reabrir_cancela(self):
        self.colab(3000)
        f = folha_svc.calcular(self.folha())
        ids = folha_svc.fechar(f, self.admin)
        contas = ContaPagar.objects.filter(pk__in=ids)
        self.assertEqual(contas.count(), 3)      # salários, INSS e FGTS (IRRF zero não gera guia)
        self.assertEqual(sum(c.valor for c in contas), D("2751.40") + D("240.00") + D("248.60"))
        sal = contas.get(descricao__contains="Salários")
        self.assertEqual(sal.vencimento, data("2026-04-05"))
        with self.assertRaises(ValidationError):
            folha_svc.calcular(f)                # fechada não recalcula
        folha_svc.reabrir(f)
        self.assertEqual(ContaPagar.objects.filter(pk__in=ids, status="cancelado").count(), 3)

    def test_reabrir_recusa_se_ja_pagou(self):
        self.colab(3000)
        f = folha_svc.calcular(self.folha())
        ids = folha_svc.fechar(f, self.admin)
        ContaPagar.objects.filter(pk=ids[0]).update(status="pago")
        with self.assertRaises(ValidationError):
            folha_svc.reabrir(f)

    def test_tabela_vigente_inexistente(self):
        f = self.folha("2020-01-01")
        self.colab(3000, adm="2019-01-01")
        with self.assertRaises(ValidationError):
            folha_svc.calcular(f)


class PontoTest(Base):
    def marcar(self, c, dia, *horas):
        for h in horas:
            hh, mm = map(int, h.split(":"))
            dt = timezone.make_aware(datetime.datetime.combine(data(dia), datetime.time(hh, mm)))
            Marcacao.objects.create(colaborador=c, momento=dt, origem="importado")

    def test_apuracao_extra_falta_abono_feriado_e_inconsistencia(self):
        c = self.colab(3000, adm="2026-01-01")
        self.marcar(c, "2026-03-02", "08:00", "12:00", "13:00", "18:00")       # segunda, 9h → +1h
        self.marcar(c, "2026-03-03", "08:00", "17:00")                         # 9h − 1h intervalo = 8h
        self.marcar(c, "2026-03-04", "08:00")                                  # ímpar
        AbonoPonto.objects.create(colaborador=c, data=data("2026-03-05"), tipo="atestado")
        Feriado.objects.create(data=data("2026-03-06"), nome="Feriado local")
        ponto.apurar_periodo(c, data("2026-03-02"), data("2026-03-08"))
        ap = {a.data: a for a in c.apuracoes.all()}
        self.assertEqual(ap[data("2026-03-02")].saldo_min, 60)
        self.assertEqual(ap[data("2026-03-03")].trabalhado_min, 480)
        self.assertTrue(ap[data("2026-03-04")].inconsistente)
        self.assertEqual(ap[data("2026-03-05")].esperado_min, 0)               # abonado
        self.assertFalse(ap[data("2026-03-05")].falta)
        self.assertEqual(ap[data("2026-03-06")].esperado_min, 0)               # feriado
        self.assertEqual(ap[data("2026-03-07")].esperado_min, 0)               # sábado
        self.assertEqual(ponto.saldo_banco(c), 60 - 480)                       # +1h do dia 2 e falta do dia 4 (ímpar=0h)

    def test_falta_e_tolerancia(self):
        c = self.colab(3000, adm="2026-01-01")
        self.marcar(c, "2026-03-09", "08:00", "12:00", "13:00", "17:05")   # 5 min a mais: dentro da tolerância
        ponto.apurar_periodo(c, data("2026-03-09"), data("2026-03-10"))
        ap = {a.data: a for a in c.apuracoes.all()}
        self.assertEqual(ap[data("2026-03-09")].saldo_min, 0)
        self.assertTrue(ap[data("2026-03-10")].falta)

    def test_bater_ponto_e_duplo_clique(self):
        c = self.colab(3000)
        ponto.bater_ponto(c, "10.0.0.1")
        with self.assertRaises(ValidationError):
            ponto.bater_ponto(c, "10.0.0.1")
        self.assertEqual(c.marcacoes.count(), 1)

    def test_importar_ponto_na_folha(self):
        c = self.colab(3000, adm="2026-01-01")
        self.marcar(c, "2026-03-02", "08:00", "12:00", "13:00", "19:00")   # +2h
        f = Folha.objects.create(empresa=self.emp, competencia=data("2026-03-01"))
        folha_svc.importar_ponto(f)
        extra = LancamentoFolha.objects.get(colaborador=c, rubrica__codigo="0100")
        self.assertEqual(extra.valor, D("3000") / D("200") * D("1.5") * 2)   # 15 × 1,5 × 2h = 45
        self.assertEqual(extra.valor, D("45.00"))
        self.assertTrue(LancamentoFolha.objects.filter(colaborador=c, rubrica__codigo="0200").exists())   # faltas


class FeriasTest(Base):
    def test_periodos_e_direito_reduzido_por_faltas(self):
        c = self.colab(3000, adm="2024-03-01")
        ferias.gerar_periodos(c, ate=data("2026-06-01"))
        self.assertEqual(c.periodos_ferias.count(), 3)
        per = c.periodos_ferias.get(inicio=data("2024-03-01"))
        self.assertEqual(per.fim, data("2025-02-28"))
        self.assertEqual(per.limite_concessivo, data("2026-02-28"))
        self.assertEqual(per.situacao, "vencido")

    def pedido(self, c, per, inicio, dias, abono=0):
        return SolicitacaoFerias(colaborador=c, periodo=per, inicio=data(inicio), dias=dias, abono_dias=abono)

    def test_regras_de_concessao(self):
        c = self.colab(3000, adm="2025-01-01")
        ferias.gerar_periodos(c, ate=data("2026-03-01"))
        per = c.periodos_ferias.get(inicio=data("2025-01-01"))
        with self.assertRaises(ValidationError):                         # antes de completar o período
            ferias.validar(self.pedido(c, per, "2025-06-01", 30))
        with self.assertRaises(ValidationError):                         # abono acima de 10 dias
            ferias.validar(self.pedido(c, per, "2026-02-01", 20, 11))
        ferias.validar(self.pedido(c, per, "2026-02-01", 20, 10))
        s = self.pedido(c, per, "2026-02-01", 14)
        s.save()
        s.status = "aprovada"
        s.save()
        with self.assertRaises(ValidationError):                         # sobreposição
            ferias.validar(self.pedido(c, per, "2026-02-10", 10))
        with self.assertRaises(ValidationError):                         # estoura o saldo
            ferias.validar(self.pedido(c, per, "2026-04-01", 20))
        ferias.validar(self.pedido(c, per, "2026-04-01", 16))

    def test_fluxo_de_aprovacao_pelo_gestor(self):
        gestor_user = User.objects.create_user("gestor", password="x")
        gestor = self.colab(8000, nome="Chefe", cpf="39053344705", usuario=gestor_user)
        c = self.colab(3000, adm="2025-01-01", gestor=gestor)
        ferias.gerar_periodos(c, ate=data("2026-03-01"))
        per = c.periodos_ferias.get(inicio=data("2025-01-01"))
        s = ferias.solicitar(self.pedido(c, per, "2026-02-02", 30))
        self.assertTrue(ferias.pode_decidir(gestor_user, s))
        self.assertFalse(ferias.pode_decidir(User.objects.create_user("outro", password="x"), s))
        with self.assertRaises(ValidationError):
            ferias.decidir(s, gestor_user, False)                        # recusa exige motivo
        ferias.decidir(s, gestor_user, True)
        self.assertEqual(s.status, "aprovada")
        self.assertEqual(per.saldo, 0)
        with self.assertRaises(ValidationError):
            ferias.decidir(s, gestor_user, True)


class RescisaoTest(Base):
    def rescisao(self, motivo, aviso="indenizado", adm="2024-01-01", desl="2026-03-15", saldo_fgts="0"):
        c = self.colab(3000, adm=adm)
        r = Rescisao.objects.create(empresa=self.emp, colaborador=c, data_desligamento=data(desl), motivo=motivo,
                                    aviso=aviso, saldo_fgts=D(saldo_fgts))
        return folha_svc.calcular_rescisao(r)

    def valor(self, r, trecho):
        return next(D(x["valor"]) for x in r.memoria if trecho in x["descricao"])

    def test_sem_justa_causa_com_aviso_indenizado(self):
        r = self.rescisao("sem_justa_causa", saldo_fgts="10000")
        self.assertEqual(self.valor(r, "Saldo de salário"), D("1500.00"))
        self.assertEqual(self.valor(r, "Aviso prévio"), D("3600.00"))          # 36 dias (2 anos completos)
        self.assertEqual(self.valor(r, "13º salário proporcional"), D("1000.00"))   # 4/12 (com projeção do aviso)
        self.assertIn("1/3 sobre férias proporcionais", " ".join(x["descricao"] for x in r.memoria))
        depositos = (D("1500") + D("1000") + D("3600")) * D("0.08")
        self.assertEqual(r.multa_fgts, ((D("10000") + depositos) * D("0.4")).quantize(D("0.01")))
        self.assertGreater(r.total_liquido, 0)

    def test_justa_causa_nao_paga_13_nem_proporcionais(self):
        r = self.rescisao("justa_causa", aviso="dispensado")
        desc = " ".join(x["descricao"] for x in r.memoria)
        self.assertNotIn("13º", desc)
        self.assertNotIn("proporcionais", desc)
        self.assertEqual(r.multa_fgts, 0)

    def test_acordo_paga_metade_do_aviso_e_multa_de_20(self):
        r = self.rescisao("acordo", saldo_fgts="5000")
        self.assertEqual(self.valor(r, "Aviso prévio"), D("1800.00"))
        self.assertTrue(r.multa_fgts > 0)
        self.assertIn("20%", r.memoria[-1]["descricao"])

    def test_pedido_de_demissao_com_aviso_nao_cumprido_desconta(self):
        r = self.rescisao("pedido", aviso="nao_cumprido")
        self.assertEqual(self.valor(r, "Desconto de aviso"), D("3000.00"))
        self.assertEqual(r.multa_fgts, 0)

    def test_efetivar_desliga_e_gera_conta(self):
        r = self.rescisao("sem_justa_causa")
        t = folha_svc.efetivar_rescisao(r)
        r.refresh_from_db()
        r.colaborador.refresh_from_db()
        self.assertEqual((r.status, r.colaborador.status, t.valor), ("efetivada", "desligado", r.total_liquido))
        self.assertEqual(t.vencimento, data("2026-03-25"))                       # 10 dias (art. 477)
        with self.assertRaises(ValidationError):
            folha_svc.efetivar_rescisao(r)


class RotinaTest(Base):
    def test_rotina_cria_periodos_e_avisa_no_prazo(self):
        from apps.core.models import Notificacao
        from apps.rh import rotinas

        c = self.colab(3000, adm="2024-03-01")
        r = rotinas.executar(hoje=data("2026-06-01"))
        self.assertGreaterEqual(r["periodos"], 2)
        # período 01/03/2024–28/02/2025 vence (concessivo) em 28/02/2026; 60 dias antes = 30/12/2025
        rotinas.executar(hoje=data("2025-12-30"))
        self.assertTrue(Notificacao.objects.filter(texto__contains="vencem em 60").exists())
        antes = Notificacao.objects.count()
        rotinas.executar(hoje=data("2025-12-31"))          # fora dos marcos: não repete o aviso
        self.assertEqual(Notificacao.objects.count(), antes)
