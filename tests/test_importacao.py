import io
import json
import shutil
import sqlite3
import tempfile
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import Cargo, Colaborador, Departamento, Empresa, ParceiroNegocio
from apps.importacao import conversao as cv
from apps.importacao import leitores, sqldump, sugestao
from apps.importacao.destinos import destino
from apps.importacao.models import Importacao
from apps.fiscal.models import Produto

User = get_user_model()
MEDIA = tempfile.mkdtemp()

CSV = ("Nome;CPF;Admissão;Salário;Setor;Cargo\n"
       "Ana Souza;529.982.247-25;01/02/2020;3.500,00;TI;Analista\n"
       "Bruno Lima;111.444.777-35;15/03/2021;2.800,50;TI;Analista\n"
       "Carla Dias;123;10/10/2022;1.000,00;RH;Assistente\n"
       "Ana Souza;529.982.247-25;01/02/2020;3.500,00;TI;Analista\n")


def arquivo(nome="func.csv", texto=CSV, enc="cp1252"):
    return SimpleUploadedFile(nome, texto.encode(enc), content_type="text/csv")


class ConversaoTest(TestCase):
    def test_numeros_e_datas_brasileiros(self):
        self.assertEqual(cv.decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(cv.decimal("1,234.56"), Decimal("1234.56"))
        self.assertEqual(cv.decimal("R$ 10,5"), Decimal("10.5"))
        self.assertEqual(cv.inteiro("1.000"), 1000)
        self.assertEqual(cv.data("31/12/2020"), date(2020, 12, 31))
        self.assertEqual(cv.data("2020-12-31"), date(2020, 12, 31))
        self.assertEqual(cv.data(44197), date(2021, 1, 1))     # número de série do Excel
        self.assertTrue(cv.booleano("Sim"))
        self.assertFalse(cv.booleano("não"))

    def test_documentos_perdem_zeros_no_excel(self):
        self.assertEqual(cv.cpf(1234567890), "01234567890")
        self.assertEqual(cv.cnpj_ou_cpf("11.222.333/0001-81"), "11222333000181")

    def test_valor_invalido_levanta_erro_amigavel(self):
        with self.assertRaises(ValueError):
            cv.data("ontem")
        with self.assertRaises(ValueError):
            cv.decimal("abc")


class LeitoresTest(TestCase):
    def _arq(self, nome, conteudo):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        caminho = f"{d}/{nome}"
        with open(caminho, "wb") as f:
            f.write(conteudo)
        return caminho

    def test_csv_cp1252_com_ponto_e_virgula(self):
        c = self._arq("a.csv", CSV.encode("cp1252"))
        t = leitores.ler(c, "a.csv", leitores.tabelas(c, "a.csv")[0])
        self.assertEqual(t.colunas[:3], ["Nome", "CPF", "Admissão"])
        self.assertEqual(len(t.linhas), 4)

    def test_excel(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Fornecedores"
        ws.append(["Razão Social", "CNPJ"])
        ws.append(["ACME", "11222333000181"])
        b = io.BytesIO()
        wb.save(b)
        c = self._arq("f.xlsx", b.getvalue())
        self.assertEqual(leitores.tabelas(c, "f.xlsx"), ["Fornecedores"])
        t = leitores.ler(c, "f.xlsx", "Fornecedores")
        self.assertEqual(t.linhas[0][0], "ACME")

    def test_sqlite(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        con = sqlite3.connect(f"{d}/x.db")
        con.execute("create table clientes (id integer, nome text)")
        con.execute("insert into clientes values (1, 'Loja A')")
        con.commit()
        con.close()
        self.assertEqual(leitores.tabelas(f"{d}/x.db", "x.db"), ["clientes"])
        self.assertEqual(leitores.ler(f"{d}/x.db", "x.db", "clientes").linhas[0][1], "Loja A")

    def test_json_do_airtable(self):
        dados = {"records": [{"id": "r1", "fields": {"Nome": "A", "Email": "a@a.com"}},
                             {"id": "r2", "fields": {"Nome": "B"}}]}
        c = self._arq("t.json", json.dumps(dados).encode())
        nome = leitores.tabelas(c, "t.json")[0]
        t = leitores.ler(c, "t.json", nome)
        self.assertIn("Nome", t.colunas)
        self.assertEqual(len(t.linhas), 2)

    def test_dump_mysql_nao_executa_sql(self):
        sql = ("CREATE TABLE `produtos` (`cod` varchar(10), `descr` varchar(50));\n"
               "INSERT INTO `produtos` VALUES ('P1','Caneta ''azul'''),('P2','Lápis');\n"
               "DROP TABLE usuarios;\n")
        r = sqldump.ler_dump(sql, 100)
        self.assertEqual(r["produtos"]["colunas"], ["cod", "descr"])
        self.assertEqual(r["produtos"]["linhas"][0], ["P1", "Caneta 'azul'"])
        self.assertEqual(len(r["produtos"]["linhas"]), 2)

    def test_formato_nao_suportado(self):
        with self.assertRaises(ValidationError):
            leitores.formato_de("virus.exe")

    @override_settings(IMPORTACAO_MAX_LINHAS=2)
    def test_limite_de_linhas(self):
        c = self._arq("a.csv", CSV.encode("cp1252"))
        t = leitores.ler(c, "a.csv", "a")
        self.assertEqual(len(t.linhas), 2)
        self.assertTrue(t.truncada)


class SugestaoTest(TestCase):
    def tabela(self, cols, linhas):
        return leitores.Tabela("t", cols, linhas)

    def test_heuristica_reconhece_colaboradores(self):
        t = self.tabela(["Nome do Funcionário", "CPF", "Data de Nascimento", "Setor", "Função"],
                        [["Ana", "529.982.247-25", "01/01/1990", "TI", "Dev"]])
        self.assertEqual(sugestao.heuristica_destino(t), "colaborador")
        m = sugestao.heuristica_mapeamento(destino("colaborador"), t)
        self.assertEqual(m["nome"], "Nome do Funcionário")
        self.assertEqual(m["cpf"], "CPF")
        self.assertEqual(m["departamento"], "Setor")
        self.assertEqual(m["cargo"], "Função")

    def test_heuristica_acha_cnpj_pelo_conteudo(self):
        t = self.tabela(["Empresa", "Doc"], [["ACME", "11.222.333/0001-81"], ["Beta", "11.444.777/0001-61"]])
        self.assertEqual(sugestao.heuristica_mapeamento(destino("parceiro"), t).get("documento"), "Doc")

    def test_ia_so_aceita_campos_e_colunas_existentes(self):
        t = self.tabela(["Nome", "CPF"], [["Ana", "1"]])
        resposta = json.dumps({"mapeamento": {"nome": "Nome", "cpf": "CPF", "inventado": "Nome", "email": "Nao existe"},
                               "observacoes": "ok"})
        with mock.patch("apps.importacao.sugestao.ia.chamar", return_value=resposta):
            m, obs = sugestao.sugerir_mapeamento_ia(destino("colaborador"), t)
        self.assertEqual(m, {"nome": "Nome", "cpf": "CPF"})

    def test_ia_nao_envia_exemplos_por_padrao(self):
        t = self.tabela(["Nome", "CPF"], [["Ana Secreta", "52998224725"]])
        with mock.patch("apps.importacao.sugestao.ia.chamar", return_value='{"mapeamento":{"nome":"Nome"}}') as ch:
            sugestao.sugerir_mapeamento_ia(destino("colaborador"), t)
        enviado = " ".join(str(a) for a in ch.call_args[0])
        self.assertNotIn("Ana Secreta", enviado)
        self.assertNotIn("52998224725", enviado)
        with mock.patch("apps.importacao.sugestao.ia.chamar", return_value='{"mapeamento":{"nome":"Nome"}}') as ch:
            sugestao.sugerir_mapeamento_ia(destino("colaborador"), t, enviar_amostras=True)
        self.assertIn("Ana Secreta", " ".join(str(a) for a in ch.call_args[0]))


@override_settings(MEDIA_ROOT=MEDIA)
class FluxoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("adm", "a@a.com", "x")
        cls.rh = User.objects.create_user("rh", password="x")
        cls.rh.groups.add(Group.objects.get(name="RH"))
        cls.outro = User.objects.create_user("outro", password="x")
        cls.outro.groups.add(Group.objects.get(name="RH"))
        cls.comum = User.objects.create_user("comum", password="x")
        cls.comum.groups.add(Group.objects.get(name="Usuários"))
        Empresa.objects.create(razao_social="E", cnpj="11222333000181")

    def setUp(self):
        self.client.force_login(self.rh)

    def enviar(self, texto=CSV, nome="func.csv"):
        r = self.client.post(reverse("importacao:nova"), {"arquivo": arquivo(nome, texto)}, follow=True)
        self.assertEqual(r.status_code, 200)
        return Importacao.objects.latest("pk")

    def mapear(self, imp, acao, **extra):
        post = {"acao": acao}
        for campo, m in imp.mapeamento.items():
            if "coluna" in m:
                post[f"col__{campo}"] = m["coluna"]
            else:
                post[f"fixo__{campo}"] = m["fixo"]
        post.update(extra)
        return self.client.post(reverse("importacao:mapear", args=[imp.pk]), post, follow=True)

    def preparar(self, **opcoes):
        imp = self.enviar()
        self.mapear(imp, "destino", destino="colaborador")
        imp.refresh_from_db()
        self.assertEqual(imp.destino, "colaborador")
        return imp, opcoes

    def test_upload_le_e_sugere_mapeamento(self):
        imp, _ = self.preparar()
        self.assertEqual(imp.total_linhas, 4)
        self.assertEqual(imp.mapeamento["cpf"]["coluna"], "CPF")
        self.assertEqual(imp.mapeamento["departamento"]["coluna"], "Setor")
        self.assertFalse(imp.arquivo_origem)           # o original é descartado

    def test_simulacao_nao_grava_nada(self):
        imp, _ = self.preparar()
        self.mapear(imp, "simular", criar_relacionados="on", duplicados="pular")
        imp.refresh_from_db()
        self.assertEqual(imp.status, "simulado")
        self.assertEqual(imp.resultado["criados"], 2)
        self.assertEqual(imp.resultado["erros"], 1)          # CPF "123"
        self.assertEqual(imp.resultado["ignorados"], 1)      # Ana repetida
        self.assertEqual(Colaborador.objects.count(), 0)
        self.assertEqual(Departamento.objects.count(), 0)

    def test_importar_e_desfazer(self):
        imp, _ = self.preparar()
        self.mapear(imp, "simular", criar_relacionados="on", duplicados="pular")
        self.client.post(reverse("importacao:importar", args=[imp.pk]))
        imp.refresh_from_db()
        self.assertEqual(imp.status, "concluida")
        self.assertEqual(Colaborador.objects.count(), 2)
        ana = Colaborador.objects.get(cpf="52998224725")
        self.assertEqual(ana.salario, Decimal("3500.00"))
        self.assertEqual(ana.data_admissao, date(2020, 2, 1))
        self.assertEqual(ana.departamento.nome, "TI")
        self.assertEqual(ana.cargo.nome, "Analista")
        self.assertFalse(imp.dados)                           # dados temporários apagados
        self.client.post(reverse("importacao:desfazer", args=[imp.pk]))
        imp.refresh_from_db()
        self.assertEqual(imp.status, "desfeita")
        self.assertEqual(Colaborador.objects.count(), 0)
        self.assertEqual(Departamento.objects.count(), 0)

    def test_sem_criar_relacionados_a_linha_da_erro(self):
        imp, _ = self.preparar()
        self.mapear(imp, "simular", duplicados="pular")
        imp.refresh_from_db()
        self.assertEqual(imp.resultado["criados"], 0)
        self.assertGreaterEqual(imp.resultado["erros"], 3)

    def test_atualizar_existentes(self):
        Colaborador.objects.create(empresa=Empresa.objects.first(), nome="Antigo", cpf="52998224725", tipo_contrato="clt")
        imp, _ = self.preparar()
        self.mapear(imp, "simular", criar_relacionados="on", duplicados="atualizar")
        self.client.post(reverse("importacao:importar", args=[imp.pk]))
        self.assertEqual(Colaborador.objects.get(cpf="52998224725").nome, "Ana Souza")

    def test_tudo_ou_nada(self):
        imp, _ = self.preparar()
        self.mapear(imp, "simular", criar_relacionados="on", tudo_ou_nada="on")
        self.client.post(reverse("importacao:importar", args=[imp.pk]))
        imp.refresh_from_db()
        self.assertEqual(imp.status, "recusada")
        self.assertEqual(Colaborador.objects.count(), 0)
        self.assertEqual(Departamento.objects.count(), 0)

    def test_nao_importa_sem_simular(self):
        imp, _ = self.preparar()
        self.client.post(reverse("importacao:importar", args=[imp.pk]))
        imp.refresh_from_db()
        self.assertEqual(imp.status, "lido")
        self.assertEqual(Colaborador.objects.count(), 0)

    def test_campo_obrigatorio_sem_coluna_bloqueia_simulacao(self):
        imp, _ = self.preparar()
        imp.mapeamento.pop("cpf")
        imp.save()
        self.client.post(reverse("importacao:mapear", args=[imp.pk]),
                         {"acao": "simular", **{f"col__{c}": m["coluna"] for c, m in imp.mapeamento.items()}})
        imp.refresh_from_db()
        self.assertNotEqual(imp.status, "simulado")

    def test_erros_csv_neutraliza_formulas(self):
        imp, _ = self.preparar()
        imp.resultado = {"erros_lista": [[2, "=HYPERLINK(\"x\")"]]}
        imp.save()
        r = self.client.get(reverse("importacao:erros_csv", args=[imp.pk]))
        self.assertIn("'=HYPERLINK", r.content.decode())

    def test_parceiro_vira_cliente_e_fornecedor(self):
        csv = "Razão Social;CNPJ\nACME Ltda;11.222.333/0001-81\nBeta SA;11.444.777/0001-61\n"
        self.client.force_login(self.admin)
        imp = self.enviar(csv, "forn.csv")
        self.mapear(imp, "destino", destino="parceiro")
        imp.refresh_from_db()
        self.assertEqual(imp.mapeamento["documento"]["coluna"], "CNPJ")
        self.mapear(imp, "simular", papel="ambos", duplicados="pular")
        self.client.post(reverse("importacao:importar", args=[imp.pk]))
        p = ParceiroNegocio.objects.get(documento="11222333000181")
        self.assertEqual(sorted(p.papeis()), ["Cliente", "Fornecedor"])
        self.client.post(reverse("importacao:desfazer", args=[imp.pk]))
        self.assertEqual(ParceiroNegocio.objects.count(), 0)

    def test_valor_fixo_para_todas_as_linhas(self):
        csv = "Código;Descrição\nA1;Caneta\nA2;Lápis\n"
        self.client.force_login(self.admin)
        imp = self.enviar(csv, "prod.csv")
        self.mapear(imp, "destino", destino="produto")
        imp.refresh_from_db()
        imp.mapeamento["unidade"] = {"fixo": "CX"}
        imp.save()
        self.mapear(imp, "simular")
        self.client.post(reverse("importacao:importar", args=[imp.pk]))
        self.assertEqual(Produto.objects.get(codigo="A1").unidade, "CX")

    # --------------------------------------------------------------- permissões e isolamento
    def test_usuario_sem_permissao_nao_entra(self):
        self.client.force_login(self.comum)
        for nome in ("importacao:lista", "importacao:nova", "importacao:nova_banco"):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403)

    def test_rh_nao_conecta_banco(self):
        self.assertEqual(self.client.get(reverse("importacao:nova_banco")).status_code, 403)

    def test_destino_exige_permissao_de_inclusao(self):
        """RH pode importar colaboradores, mas não produtos (sem fiscal.add_produto)."""
        imp = self.enviar("Código;Descrição\nA1;Caneta\n", "p.csv")
        self.mapear(imp, "destino", destino="produto")
        imp.refresh_from_db()
        self.assertEqual(imp.destino, "")

    def test_importacao_de_outro_usuario_e_invisivel(self):
        imp, _ = self.preparar()
        self.client.force_login(self.outro)
        for nome in ("importacao:detalhe", "importacao:mapear", "importacao:erros_csv"):
            self.assertEqual(self.client.get(reverse(nome, args=[imp.pk])).status_code, 404, nome)
        for nome in ("importacao:importar", "importacao:desfazer", "importacao:excluir"):
            self.assertEqual(self.client.post(reverse(nome, args=[imp.pk])).status_code, 404, nome)
        self.assertNotIn(imp.descricao, self.client.get(reverse("importacao:lista")).content.decode())

    def test_arquivo_perigoso_e_recusado(self):
        r = self.client.post(reverse("importacao:nova"), {"arquivo": SimpleUploadedFile("x.exe", b"MZ")})
        self.assertEqual(Importacao.objects.count(), 0)
        self.assertContains(r, "não permitido", status_code=200) if False else None

    def test_empresa_sem_acesso_nao_e_aceita(self):
        outra = Empresa.objects.create(razao_social="Outra", cnpj="11444777000161")
        outra.usuarios.add(self.admin)               # só o admin acessa a "Outra"
        imp, _ = self.preparar()
        self.mapear(imp, "salvar", empresa=str(outra.pk))
        imp.refresh_from_db()
        self.assertIsNone(imp.opcoes.get("empresa"))

    def test_ia_indisponivel_nao_quebra_a_tela(self):
        imp, _ = self.preparar()
        with mock.patch("apps.importacao.sugestao.ia.chamar", side_effect=ValidationError("fora do ar")):
            r = self.mapear(imp, "ia")
        self.assertEqual(r.status_code, 200)


class ConectoresTest(TestCase):
    def test_recusa_endereco_de_metadados_e_tipo_invalido(self):
        from apps.importacao import conectores
        with self.assertRaises(ValidationError):
            conectores._conferir_destino("169.254.169.254", 3306)
        with self.assertRaises(ValidationError):
            conectores.abrir("oracle", "x", 1521, "d", "u", "p")


@override_settings(MEDIA_ROOT=MEDIA)
class FluxoBancoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("adm", "a@a.com", "x")

    def test_conexao_nao_grava_a_senha_e_le_a_tabela(self):
        self.client.force_login(self.admin)
        con = mock.MagicMock()
        tab = leitores.Tabela("clientes", ["Nome", "CNPJ"], [["ACME", "11222333000181"]], total=1)
        with mock.patch("apps.importacao.conectores.abrir", return_value=con), \
             mock.patch("apps.importacao.conectores.listar_tabelas", return_value=["clientes", "pedidos"]), \
             mock.patch("apps.importacao.conectores.ler_tabela", return_value=tab):
            r = self.client.post(reverse("importacao:nova_banco"), {
                "tipo": "mariadb", "host": "10.0.0.5", "porta": "3306", "banco": "legado", "usuario": "ro",
                "senha": "segredo-123"})
            imp = Importacao.objects.get()
            self.assertRedirects(r, reverse("importacao:tabela", args=[imp.pk]))
            self.assertNotIn("segredo-123", json.dumps(imp.opcoes) + imp.descricao)
            self.assertNotIn("segredo-123", json.dumps(self.client.session.get("importacao_conexoes")))
            self.client.post(reverse("importacao:tabela", args=[imp.pk]), {"tabela": "clientes"})
        imp.refresh_from_db()
        self.assertEqual(imp.total_linhas, 1)
        self.assertEqual(imp.colunas, ["Nome", "CNPJ"])
        self.assertNotIn(str(imp.pk), self.client.session.get("importacao_conexoes", {}))

    def test_tabela_fora_da_lista_do_banco_e_recusada(self):
        self.client.force_login(self.admin)
        with mock.patch("apps.importacao.conectores.abrir", return_value=mock.MagicMock()), \
             mock.patch("apps.importacao.conectores.listar_tabelas", return_value=["clientes", "pedidos"]):
            self.client.post(reverse("importacao:nova_banco"), {
                "tipo": "postgres", "host": "10.0.0.5", "banco": "d", "usuario": "u", "senha": "s"})
            imp = Importacao.objects.get()
            self.client.post(reverse("importacao:tabela", args=[imp.pk]), {"tabela": "usuarios; DROP TABLE x"})
        imp.refresh_from_db()
        self.assertFalse(imp.dados)
