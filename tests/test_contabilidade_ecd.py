import datetime
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.contabilidade import servicos
from apps.contabilidade.ecd import gerar_ecd
from apps.contabilidade.encerramento import encerrar_exercicio, encerramento_do_ano
from apps.contabilidade.models import ContaContabil, Lancamento, PeriodoFechado
from apps.contabilidade.views import resultado
from apps.core.models import Empresa

ANO = 2025
SIG = [{"nome": "Maria Contadora", "cpf": "52998224725", "qualificacao": "Contador", "codigo": "900",
        "crc": "1SP123456", "uf_crc": "SP"},
       {"nome": "João Diretor", "cpf": "11144477735", "qualificacao": "Administrador", "codigo": "203",
        "responsavel_legal": True}]


def conta(cod):
    return ContaContabil.objects.get(codigo=cod)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.emp = Empresa.objects.create(razao_social="Guará Comércio Ltda", cnpj="11222333000181", uf="SP",
                                         codigo_municipio="3550308", cidade="São Paulo")
        d = datetime.date
        servicos.gravar(d(ANO, 1, 10), "Integralização", conta("1.1.1"), conta("2.3.1"), D("10000"), empresa=cls.emp)
        servicos.gravar(d(ANO, 2, 5), "Venda a prazo | teste", conta("1.1.2"), conta("3.1"), D("5000"), empresa=cls.emp)
        servicos.gravar(d(ANO, 3, 7), "Aluguel", conta("4.2"), conta("1.1.1"), D("1200"), empresa=cls.emp)
        servicos.gravar(d(ANO, 12, 15), "Juros", conta("4.5"), conta("1.1.1"), D("300"), empresa=cls.emp)


class EncerramentoTest(Base):
    def test_apura_lucro_e_zera_resultado(self):
        l = encerrar_exercicio(ANO, self.emp)
        self.assertEqual(l.evento, "encerramento_exercicio")
        self.assertEqual(l.data, datetime.date(ANO, 12, 31))
        lucros = servicos.saldos(empresa=self.emp)[conta("2.3.2").pk]
        self.assertEqual(lucros[1] - lucros[0], D("3500"))
        for cod in ("3.1", "4.2", "4.5"):
            d, c = servicos.saldos(empresa=self.emp)[conta(cod).pk]
            self.assertEqual(d, c)
        self.assertEqual(l.partidas.filter(tipo="D").aggregate(t=__import__("django.db.models", fromlist=["Sum"]).Sum("valor"))["t"],
                         l.partidas.filter(tipo="C").aggregate(t=__import__("django.db.models", fromlist=["Sum"]).Sum("valor"))["t"])

    def test_dre_ignora_o_encerramento(self):
        encerrar_exercicio(ANO, self.emp)
        r = resultado(datetime.date(ANO, 1, 1), datetime.date(ANO, 12, 31), self.emp, sem_encerramento=True)
        self.assertEqual(r["resultado"], D("3500"))

    def test_nao_encerra_duas_vezes_e_estorno_reabre(self):
        l = encerrar_exercicio(ANO, self.emp)
        with self.assertRaises(ValidationError):
            encerrar_exercicio(ANO, self.emp)
        l.estornar()
        self.assertIsNone(encerramento_do_ano(ANO, self.emp))
        encerrar_exercicio(ANO, self.emp)

    def test_periodo_fechado_bloqueia(self):
        PeriodoFechado.objects.create(ano=ANO, mes=12)
        with self.assertRaises(ValidationError):
            encerrar_exercicio(ANO, self.emp)

    def test_prejuizo(self):
        servicos.gravar(datetime.date(2024, 5, 1), "Despesa", conta("4.2"), conta("1.1.1"), D("700"), empresa=self.emp)
        encerrar_exercicio(2024, self.emp)
        d, c = servicos.saldos(empresa=self.emp)[conta("2.3.2").pk]
        self.assertEqual(d - c, D("700"))

    def test_sem_resultado(self):
        with self.assertRaises(ValidationError):
            encerrar_exercicio(2020, self.emp)


class EcdTest(Base):
    def arquivo(self):
        encerrar_exercicio(ANO, self.emp)
        nome, dados = gerar_ecd(self.emp, ANO, SIG)
        return nome, dados.decode("latin-1").split("\r\n")[:-1]

    def test_exige_encerramento(self):
        with self.assertRaises(ValidationError) as cm:
            gerar_ecd(self.emp, ANO, SIG)
        self.assertIn("Encerre o exercício", " ".join(cm.exception.messages))

    def test_exige_dados_da_empresa(self):
        encerrar_exercicio(ANO, self.emp)
        self.emp.uf = ""
        self.emp.codigo_municipio = ""
        self.emp.save()
        with self.assertRaises(ValidationError) as cm:
            gerar_ecd(self.emp, ANO, SIG)
        t = " ".join(cm.exception.messages)
        self.assertIn("UF", t); self.assertIn("IBGE", t)

    def test_exige_signatario(self):
        encerrar_exercicio(ANO, self.emp)
        with self.assertRaises(ValidationError):
            gerar_ecd(self.emp, ANO, [])

    def test_estrutura_e_contagens(self):
        nome, linhas = self.arquivo()
        self.assertEqual(nome, f"ECD_11222333000181_{ANO}0101_{ANO}1231.txt")
        self.assertTrue(linhas[0].startswith("|0000|LECD|01012025|31122025|Guará Comércio Ltda|11222333000181|SP|"))
        self.assertEqual(linhas[0].count("|"), 25)
        self.assertEqual(linhas[-1], f"|9999|{len(linhas)}|")
        # cada 9900 confere com a contagem real
        reais = {}
        for l in linhas:
            reais[l.split("|")[1]] = reais.get(l.split("|")[1], 0) + 1
        for l in linhas:
            if l.startswith("|9900|"):
                _, _, tipo, qtd, _ = l.split("|")
                self.assertEqual(int(qtd), reais[tipo], tipo)
        # totalizadores de bloco
        for reg, prefixos in (("0990", "0"), ("I990", "I"), ("J990", "J"), ("9990", "9")):
            n = sum(1 for l in linhas if l[1] == prefixos)
            self.assertIn(f"|{reg}|{n}|", linhas)
        self.assertTrue(all(l.startswith("|") and l.endswith("|") for l in linhas))
        self.assertFalse(any("||||||||" in l for l in linhas[:1]))

    def test_termos_declaram_total_de_linhas(self):
        _, linhas = self.arquivo()
        total = len(linhas)
        i030 = next(l for l in linhas if l.startswith("|I030|")).split("|")
        j900 = next(l for l in linhas if l.startswith("|J900|")).split("|")
        self.assertEqual(i030[5], str(total))
        self.assertEqual(j900[6], str(total))

    def test_diario_balanceado_e_historico_sem_pipe(self):
        _, linhas = self.arquivo()
        n_lancs = Lancamento.objects.filter(empresa=self.emp).count()
        self.assertEqual(sum(1 for l in linhas if l.startswith("|I200|")), n_lancs)
        deb = cred = D("0")
        for l in linhas:
            if l.startswith("|I250|"):
                p = l.split("|")
                v = D(p[4].replace(",", "."))
                if p[5] == "D": deb += v
                else: cred += v
        self.assertEqual(deb, cred)
        self.assertTrue(any("Venda a prazo   teste" in l or "Venda a prazo  teste" in l for l in linhas))
        self.assertEqual(sum(1 for l in linhas if l.startswith("|I200|") and l.split("|")[5] == "E"), 1)

    def test_plano_de_contas_com_niveis_e_superior(self):
        _, linhas = self.arquivo()
        i050 = [l.split("|") for l in linhas if l.startswith("|I050|")]
        self.assertEqual(len(i050), ContaContabil.objects.count())
        c = next(p for p in i050 if p[6] == "1.1.1")
        self.assertEqual((c[3], c[4], c[5], c[7]), ("01", "A", "3", "1.1"))
        raiz = next(p for p in i050 if p[6] == "1")
        self.assertEqual((raiz[4], raiz[5], raiz[7]), ("S", "1", ""))

    def test_balancete_dezembro_e_apuracao(self):
        _, linhas = self.arquivo()
        i150 = [i for i, l in enumerate(linhas) if l.startswith("|I150|")]
        self.assertEqual(len(i150), 12)
        # bloco de dezembro: caixa 10000 - 1200 - 300 = 8500 devedor no final
        dez = linhas[i150[-1]:]
        caixa = next(l for l in dez if l.startswith("|I155|1.1.1|"))
        self.assertTrue(caixa.endswith("|8500,00|D|"), caixa)
        self.assertIn("|I350|31122025|", linhas)
        i355 = {l.split("|")[2]: l.split("|") for l in linhas if l.startswith("|I355|")}
        self.assertEqual((i355["3.1"][4], i355["3.1"][5]), ("5000,00", "C"))
        self.assertEqual((i355["4.2"][4], i355["4.2"][5]), ("1200,00", "D"))

    def test_balanco_e_dre(self):
        _, linhas = self.arquivo()
        j100 = {l.split("|")[2]: l.split("|") for l in linhas if l.startswith("|J100|")}
        self.assertEqual((j100["1"][10], j100["1"][11]), ("13500,00", "D"))
        self.assertEqual(j100["1"][6], "A")
        self.assertEqual((j100["2.3.2"][10], j100["2.3.2"][11]), ("3500,00", "C"))
        total_passivo_pl = D(j100["2"][10].replace(",", "."))
        self.assertEqual(total_passivo_pl, D("13500"))
        j150 = {l.split("|")[3]: l.split("|") for l in linhas if l.startswith("|J150|")}
        self.assertEqual((j150["3.1"][10], j150["3.1"][11], j150["3.1"][12]), ("5000,00", "C", "R"))
        self.assertEqual(j150["4.2"][12], "D")

    def test_signatarios(self):
        _, linhas = self.arquivo()
        j930 = [l.split("|") for l in linhas if l.startswith("|J930|")]
        self.assertEqual(len(j930), 2)
        self.assertEqual(j930[0][2:5], ["Maria Contadora", "52998224725", "Contador"])
        self.assertEqual(j930[1][-2], "S")

    def test_outra_empresa_nao_entra(self):
        outra = Empresa.objects.create(razao_social="Outra SA", cnpj="45723174000110", uf="SP", codigo_municipio="3550308")
        servicos.gravar(datetime.date(ANO, 4, 1), "Outra empresa", conta("1.1.1"), conta("2.3.1"), D("99"), empresa=outra)
        _, linhas = self.arquivo()
        self.assertFalse(any("Outra empresa" in l for l in linhas))

    def test_exercicio_anterior_nao_encerrado_bloqueia(self):
        servicos.gravar(datetime.date(ANO - 1, 5, 1), "Despesa antiga", conta("4.2"), conta("1.1.1"), D("10"), empresa=self.emp)
        encerrar_exercicio(ANO, self.emp)
        with self.assertRaises(ValidationError) as cm:
            gerar_ecd(self.emp, ANO, SIG)
        self.assertIn("exercícios anteriores", " ".join(cm.exception.messages))


class TelasTest(Base):
    def setUp(self):
        self.u = User.objects.create_user("ctb", password="x")
        self.u.groups.add(Group.objects.get(name="Contabilidade"))
        self.client.force_login(self.u)

    def test_encerrar_pela_tela_e_baixar_ecd(self):
        r = self.client.post(reverse("contabilidade:encerrar_exercicio"),
                             {"ano": ANO, "empresa": self.emp.pk, "acao": "encerrar"})
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse("contabilidade:gerar_ecd"), {
            "empresa": self.emp.pk, "ano": ANO, "contador_nome": "Maria Contadora", "contador_cpf": "52998224725",
            "resp_nome": "João Diretor", "resp_cpf": "11144477735", "resp_qualificacao": "Administrador", "ordem": 1})
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertTrue(r.content.startswith(b"|0000|LECD|"))

    def test_telas_abrem_e_exigem_permissao(self):
        for n in ("encerrar_exercicio", "gerar_ecd"):
            self.assertEqual(self.client.get(reverse(f"contabilidade:{n}")).status_code, 200)
        self.client.force_login(User.objects.create_user("sem", password="x"))
        self.assertEqual(self.client.get(reverse("contabilidade:gerar_ecd")).status_code, 403)
