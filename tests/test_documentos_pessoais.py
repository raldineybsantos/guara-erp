import datetime

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Colaborador, Empresa
from apps.core.telas import ColaboradorForm
from apps.rh.documentos_pessoais import DocumentoPessoal, TipoDocumentoPessoal, idade_em, pendencias
from apps.rh.models import Dependente
from apps.rh.telas import DependenteForm, DocumentoPessoalForm

HOJE = datetime.date.today()


def anos_atras(n):
    return HOJE.replace(year=HOJE.year - n) if not (HOJE.month == 2 and HOJE.day == 29) else HOJE.replace(year=HOJE.year - n, day=28)


class Base(TestCase):
    def setUp(self):
        self.emp = Empresa.objects.create(razao_social="E", cnpj="11222333000181")
        self.c = Colaborador.objects.create(empresa=self.emp, nome="Ana", cpf="52998224725", tipo_contrato="clt")

    def tipo(self, nome):
        return TipoDocumentoPessoal.objects.get(nome=nome)


class CatalogoPendenciasTest(Base):
    def test_catalogo_padrao_criado_pela_migracao(self):
        self.assertTrue(TipoDocumentoPessoal.objects.filter(nome="Carteira de vacinação", alvo="colaborador").exists())
        self.assertGreaterEqual(TipoDocumentoPessoal.objects.count(), 15)

    def test_idade(self):
        self.assertEqual(idade_em(anos_atras(7)), 7)

    def test_colaborador_sem_dependentes_so_cobra_os_proprios(self):
        alvos = {p["alvo"] for p in pendencias(self.c)}
        self.assertEqual(alvos, {"Ana"})
        nomes = {p["tipo"].nome for p in pendencias(self.c)}
        self.assertIn("RG ou CNH", nomes)
        self.assertIn("Carteira de Trabalho (CTPS)", nomes)
        self.assertIn("Carteira de vacinação", nomes)

    def test_pj_nao_precisa_de_ctps_nem_aso(self):
        self.c.tipo_contrato = "pj"
        nomes = {p["tipo"].nome for p in pendencias(self.c)}
        self.assertNotIn("Carteira de Trabalho (CTPS)", nomes)
        self.assertNotIn("ASO admissional", nomes)

    def test_conjuge_so_se_houver(self):
        antes = {p["alvo"] for p in pendencias(self.c)}
        Dependente.objects.create(colaborador=self.c, nome="Beto", parentesco="conjuge", data_nascimento=anos_atras(30))
        depois = {p["alvo"] for p in pendencias(self.c)}
        self.assertNotIn("Beto (cônjuge)", antes)
        self.assertIn("Beto (cônjuge)", depois)

    def test_filho_vacinacao_so_ate_7_anos_e_escola_a_partir_de_4(self):
        Dependente.objects.create(colaborador=self.c, nome="Bebê", parentesco="filho", data_nascimento=anos_atras(1))
        Dependente.objects.create(colaborador=self.c, nome="Teen", parentesco="filho", data_nascimento=anos_atras(12))
        por = {}
        for p in pendencias(self.c):
            por.setdefault(p["alvo"], set()).add(p["tipo"].nome)
        self.assertIn("Carteira de vacinação do filho", por["Bebê (filho/a)"])
        self.assertNotIn("Comprovante de matrícula escolar", por["Bebê (filho/a)"])
        self.assertNotIn("Carteira de vacinação do filho", por["Teen (filho/a)"])
        self.assertIn("Comprovante de matrícula escolar", por["Teen (filho/a)"])

    def test_documento_enviado_tira_a_pendencia_e_vencido_volta(self):
        t = self.tipo("RG ou CNH")
        DocumentoPessoal.objects.create(colaborador=self.c, tipo=t)
        self.assertNotIn("RG ou CNH", {p["tipo"].nome for p in pendencias(self.c)})
        aso = self.tipo("ASO admissional")
        DocumentoPessoal.objects.create(colaborador=self.c, tipo=aso, validade=HOJE - datetime.timedelta(days=1))
        achado = [p for p in pendencias(self.c) if p["tipo"] == aso]
        self.assertEqual(achado[0]["motivo"], "vencido")


class ValidacaoColaboradorTest(Base):
    def dados(self, **kw):
        d = dict(nome="Novo", cpf="11144477735", rg="123456", data_nascimento=anos_atras(30), email="a@b.com",
                 telefone="(11) 98888-7777", empresa=self.emp.pk, tipo_contrato="clt", status="ativo",
                 data_admissao=HOJE, salario="3000", chave_pix="a@b.com", cep="01001-000", logradouro="Rua A",
                 numero="10", bairro="Centro", cidade="São Paulo", uf="SP")
        d["departamento"] = self._dep()
        d["cargo"] = self._cargo()
        d.update(kw)
        return d

    def _dep(self):
        from apps.core.models import Departamento
        return Departamento.objects.get_or_create(nome="TI")[0].pk

    def _cargo(self):
        from apps.core.models import Cargo
        return Cargo.objects.get_or_create(nome="Analista")[0].pk

    def test_cadastro_completo_valido(self):
        f = ColaboradorForm(self.dados())
        self.assertTrue(f.is_valid(), f.errors)

    def test_campos_obrigatorios(self):
        f = ColaboradorForm({"nome": "X", "cpf": "11144477735", "tipo_contrato": "clt", "status": "ativo",
                             "data_admissao": HOJE, "salario": "0"})
        self.assertFalse(f.is_valid())
        for campo in ("rg", "data_nascimento", "email", "telefone", "departamento", "cargo", "cep", "uf", "salario"):
            self.assertIn(campo, f.errors)

    def test_regras_de_data_e_contato(self):
        f = ColaboradorForm(self.dados(data_nascimento=anos_atras(10), telefone="123", cep="123"))
        self.assertFalse(f.is_valid())
        self.assertIn("data_nascimento", f.errors)
        self.assertIn("telefone", f.errors)
        self.assertIn("cep", f.errors)

    def test_desligamento(self):
        self.assertIn("data_desligamento", ColaboradorForm(self.dados(status="desligado")).errors)
        f = ColaboradorForm(self.dados(data_desligamento=HOJE - datetime.timedelta(days=5)))
        self.assertIn("status", f.errors)
        f = ColaboradorForm(self.dados(status="desligado", data_desligamento=HOJE - datetime.timedelta(days=5),
                                       data_admissao=HOJE - datetime.timedelta(days=100)))
        self.assertTrue(f.is_valid(), f.errors)

    def test_banco_incompleto_e_sem_forma_de_pagamento(self):
        f = ColaboradorForm(self.dados(banco="341", chave_pix=""))
        self.assertIn("conta", f.errors)
        f = ColaboradorForm(self.dados(chave_pix=""))
        self.assertIn("chave_pix", f.errors)


class ValidacaoDependenteTest(Base):
    def test_cpf_obrigatorio_para_dedução(self):
        f = DependenteForm({"colaborador": self.c.pk, "nome": "Fi", "parentesco": "filho",
                            "data_nascimento": anos_atras(5), "deduz_irrf": "on"})
        self.assertIn("cpf", f.errors)

    def test_nascimento_futuro_e_conjuge_menor(self):
        f = DependenteForm({"colaborador": self.c.pk, "nome": "Fi", "parentesco": "filho",
                            "data_nascimento": HOJE + datetime.timedelta(days=3)})
        self.assertIn("data_nascimento", f.errors)
        f = DependenteForm({"colaborador": self.c.pk, "nome": "C", "parentesco": "conjuge",
                            "data_nascimento": anos_atras(10)})
        self.assertIn("data_nascimento", f.errors)


class AnexoTest(Base):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser("adm", "a@a.com", "x")
        self.client.force_login(self.user)

    def test_anexo_cria_documento_confidencial_no_ged(self):
        arq = SimpleUploadedFile("rg.pdf", b"%PDF-1.4 teste", content_type="application/pdf")
        r = self.client.post(reverse("rh:documentopessoal_create"), {
            "colaborador": self.c.pk, "tipo": self.tipo("RG ou CNH").pk, "numero": "123", "arquivo": arq})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        d = DocumentoPessoal.objects.get()
        self.assertTrue(d.documento.confidencial)
        self.assertEqual(d.documento.pasta.nome, "RH — Documentos pessoais")
        self.assertEqual(d.documento.num_versao, 1)
        self.assertNotIn("RG ou CNH", {p["tipo"].nome for p in pendencias(self.c)})

    def test_arquivo_obrigatorio_e_executavel_bloqueado(self):
        r = self.client.post(reverse("rh:documentopessoal_create"), {"colaborador": self.c.pk, "tipo": self.tipo("RG ou CNH").pk})
        self.assertEqual(r.status_code, 200)
        self.assertIn("arquivo", r.context["form"].errors)
        exe = SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")
        r = self.client.post(reverse("rh:documentopessoal_create"), {"colaborador": self.c.pk, "tipo": self.tipo("RG ou CNH").pk, "arquivo": exe})
        self.assertIn("arquivo", r.context["form"].errors)

    def test_documento_de_filho_exige_dependente(self):
        arq = SimpleUploadedFile("c.pdf", b"%PDF-1.4", content_type="application/pdf")
        f = DocumentoPessoalForm({"colaborador": self.c.pk, "tipo": self.tipo("Certidão de nascimento do filho").pk},
                                 {"arquivo": arq})
        self.assertIn("dependente", f.errors)

    def test_aso_exige_validade(self):
        arq = SimpleUploadedFile("a.pdf", b"%PDF-1.4", content_type="application/pdf")
        f = DocumentoPessoalForm({"colaborador": self.c.pk, "tipo": self.tipo("ASO admissional").pk}, {"arquivo": arq})
        self.assertIn("validade", f.errors)

    def test_telas_de_pendencias_e_detalhe_do_colaborador(self):
        r = self.client.get(reverse("rh:pendencias_documentos"))
        self.assertContains(r, "Ana")
        r = self.client.get(reverse("core:colaborador_detail", args=[self.c.pk]))
        self.assertContains(r, "Documentos pendentes")


class AjustesDaRodadaTest(ValidacaoColaboradorTest):
    def test_gestor_nao_lista_a_si_mesmo_nem_aceita_ciclo(self):
        chefe = Colaborador.objects.create(empresa=self.emp, nome="Chefe", cpf="11144477735", gestor=self.c)
        f = ColaboradorForm(instance=self.c)
        self.assertNotIn(self.c.pk, [g.pk for g in f.fields["gestor"].queryset])
        dados = self.dados(gestor=chefe.pk, cpf=self.c.cpf, nome="Ana")
        f = ColaboradorForm(dados, instance=self.c)
        self.assertIn("gestor", f.errors)

    def test_dependente_do_documento_filtrado_pelo_colaborador(self):
        outro = Colaborador.objects.create(empresa=self.emp, nome="Outro", cpf="11144477735")
        d1 = Dependente.objects.create(colaborador=self.c, nome="F1", parentesco="filho", data_nascimento=anos_atras(3))
        Dependente.objects.create(colaborador=outro, nome="F2", parentesco="filho", data_nascimento=anos_atras(3))
        f = DocumentoPessoalForm(initial={"colaborador": self.c.pk})
        self.assertEqual([d.pk for d in f.fields["dependente"].queryset], [d1.pk])

    def test_detalhe_formata_cpf_e_telefone(self):
        from apps.core.crud import valor_exibicao
        self.c.telefone = "11987654321"
        self.assertEqual(valor_exibicao(self.c, "cpf"), "529.982.247-25")
        self.assertEqual(valor_exibicao(self.c, "telefone"), "(11) 98765-4321")
