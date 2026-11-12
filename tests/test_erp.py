import datetime
import io
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cobranca.models import Cobranca, ReguaCobranca
from apps.cobranca.pix import _crc16, gerar_pix
from apps.core.crud import REGISTRO
from apps.core.models import Cliente, Empresa, Fornecedor, LogAuditoria
from apps.core.validators import cnpj_valido, cpf_valido
from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber
from apps.fiscal.models import NotaFiscal
from apps.ged.models import Documento, Pasta
from apps.workflow.models import Instancia, Processo

HOJE = timezone.localdate()

XML_NFE = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe35240112345678000195550010000012341000012345" versao="4.00">
<ide><natOp>VENDA</natOp><mod>55</mod><serie>1</serie><nNF>1234</nNF><dhEmi>2026-10-05T10:00:00-03:00</dhEmi></ide>
<emit><CNPJ>11222333000181</CNPJ><xNome>Fornecedor XML Ltda</xNome><xFant>Forn XML</xFant>
<enderEmit><xLgr>Rua A</xLgr><nro>10</nro><xBairro>Centro</xBairro><xMun>Campinas</xMun><UF>SP</UF><CEP>13000000</CEP></enderEmit><IE>123</IE></emit>
<det nItem="1"><prod><cProd>1</cProd><xProd>Parafuso</xProd><NCM>73181500</NCM><CFOP>5102</CFOP><qCom>100.0000</qCom><vUnCom>0.5000</vUnCom></prod>
<imposto><ICMS><ICMS00><pICMS>18.00</pICMS></ICMS00></ICMS><PIS><PISAliq><pPIS>1.65</pPIS></PISAliq></PIS></imposto></det>
<total><ICMSTot><vFrete>10.00</vFrete><vDesc>0.00</vDesc></ICMSTot></total>
</infNFe></NFe><protNFe><infProt><nProt>135240000000001</nProt></infProt></protNFe></nfeProc>"""


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")
        cls.empresa = Empresa.objects.create(razao_social="Teste Ltda", cnpj="11222333000181",
                                             chave_pix="pix@teste.com", cidade="São Paulo")
        cls.banco = ContaBancaria.objects.create(nome="Banco", saldo_inicial=Decimal("1000"))
        cls.forn = Fornecedor.objects.create(nome="Fornecedor A", documento="11444777000161")
        cls.cli = Cliente.objects.create(nome="Cliente A", email="cli@a.com")

    def usuario(self, nome, grupo):
        u = User.objects.create_user(nome, password="x")
        u.groups.add(Group.objects.get(name=grupo))
        return u


class ValidadoresTest(TestCase):
    def test_cpf_cnpj(self):
        self.assertTrue(cpf_valido("529.982.247-25"))
        self.assertFalse(cpf_valido("111.111.111-11"))
        self.assertTrue(cnpj_valido("11.222.333/0001-81"))
        self.assertFalse(cnpj_valido("11.222.333/0001-80"))

    def test_pix_crc(self):
        p = gerar_pix("pix@teste.com", "Empresa Ação", "São Paulo", Decimal("10.50"), "ERP1")
        self.assertTrue(p.startswith("000201"))
        self.assertIn("540510.50", p)  # campo 54 = valor
        self.assertEqual(p[-4:], _crc16(p[:-4]))


class FinanceiroTest(Base):
    def test_baixa_parcial_total_e_estorno(self):
        cp = ContaPagar.objects.create(fornecedor=self.forn, descricao="Aluguel", valor=Decimal("300"), vencimento=HOJE)
        cp.baixar(Decimal("100"), HOJE, self.banco)
        self.assertEqual(cp.status, "parcial")
        self.assertEqual(self.banco.saldo_atual, Decimal("900"))
        cp.baixar(Decimal("200"), HOJE, self.banco)
        self.assertEqual(cp.status, "pago")
        self.assertEqual(self.banco.saldo_atual, Decimal("700"))
        cp.estornar()
        self.assertEqual(cp.status, "aberto")
        self.assertEqual(self.banco.saldo_atual, Decimal("1000"))

    def test_baixa_acima_do_saldo_falha(self):
        from django.core.exceptions import ValidationError
        cr = ContaReceber.objects.create(cliente=self.cli, descricao="Serviço", valor=Decimal("50"), vencimento=HOJE)
        with self.assertRaises(ValidationError):
            cr.baixar(Decimal("60"), HOJE, self.banco)

    def test_parcelamento_pela_tela(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("financeiro:contapagar_create"), {
            "fornecedor": self.forn.pk, "descricao": "Equipamento", "valor": "1000.00", "juros": "0",
            "desconto": "0", "data_emissao": HOJE.isoformat(), "vencimento": HOJE.isoformat(),
            "parcelas": "3", "intervalo_dias": "30",
        })
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        parcelas = ContaPagar.objects.filter(descricao="Equipamento").order_by("parcela")
        self.assertEqual(parcelas.count(), 3)
        self.assertEqual(sum(p.valor for p in parcelas), Decimal("1000.00"))
        self.assertEqual(parcelas[2].vencimento, HOJE + datetime.timedelta(days=60))

    def test_baixa_pela_tela_e_permissao(self):
        cr = ContaReceber.objects.create(cliente=self.cli, descricao="Venda", valor=Decimal("80"), vencimento=HOJE)
        rh = self.usuario("rh1", "RH")
        self.client.force_login(rh)
        self.client.post(reverse("financeiro:titulo_baixar_receber", args=[cr.pk]),
                         {"valor": "80", "data": HOJE.isoformat(), "conta_bancaria": self.banco.pk})
        cr.refresh_from_db()
        self.assertEqual(cr.status, "aberto")  # RH não pode baixar
        fin = self.usuario("fin1", "Financeiro")
        self.client.force_login(fin)
        self.client.post(reverse("financeiro:titulo_baixar_receber", args=[cr.pk]),
                         {"valor": "80", "data": HOJE.isoformat(), "conta_bancaria": self.banco.pk})
        cr.refresh_from_db()
        self.assertEqual(cr.status, "pago")

    def test_auditoria(self):
        cp = ContaPagar.objects.create(fornecedor=self.forn, descricao="X", valor=Decimal("10"), vencimento=HOJE)
        cp.valor = Decimal("20")
        cp.save()
        log = LogAuditoria.objects.filter(modelo="financeiro.contapagar", acao="alterar").first()
        self.assertEqual(log.alteracoes["valor"], ["10.00", "20.00"])


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CobrancaTest(Base):
    def test_cobranca_pix_regua_e_sincronizacao(self):
        cr = ContaReceber.objects.create(cliente=self.cli, descricao="Mensalidade", valor=Decimal("150"),
                                         vencimento=HOJE + datetime.timedelta(days=3))
        cob = Cobranca.objects.create(conta_receber=cr, valor=cr.valor, vencimento=cr.vencimento)
        self.assertTrue(cob.pix_copia_cola.startswith("000201"))
        n = ReguaCobranca.executar()  # regra de -3 dias
        self.assertEqual(n, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Mensalidade", mail.outbox[0].subject)
        self.assertEqual(ReguaCobranca.executar(), 0)  # não reenvia a mesma regra
        cr.baixar(cr.saldo, HOJE, self.banco)
        cob.refresh_from_db()
        self.assertEqual(cob.status, "paga")


class FiscalTest(Base):
    def test_importar_xml_nfe(self):
        self.client.force_login(self.admin)
        arq = SimpleUploadedFile("nota.xml", XML_NFE.encode(), content_type="text/xml")
        r = self.client.post(reverse("fiscal:importar_xml"), {"arquivos": arq, "gerar_titulo": "on"})
        self.assertEqual(r.status_code, 302)
        nf = NotaFiscal.objects.get(numero=1234)
        self.assertEqual(nf.tipo, "entrada")
        self.assertEqual(nf.valor_produtos, Decimal("50.00"))
        self.assertEqual(nf.valor_icms, Decimal("9.00"))
        self.assertEqual(nf.valor_total, Decimal("60.00"))
        self.assertTrue(ContaPagar.objects.filter(fornecedor=nf.fornecedor, valor=Decimal("60.00")).exists())
        # importar de novo deve falhar
        arq = SimpleUploadedFile("nota.xml", XML_NFE.encode(), content_type="text/xml")
        r = self.client.post(reverse("fiscal:importar_xml"), {"arquivos": arq})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(NotaFiscal.objects.filter(numero=1234).count(), 1)

    def test_nota_com_itens_e_emissao(self):
        self.client.force_login(self.admin)
        dados = {"tipo": "saida", "modelo": "55", "serie": "1", "data_emissao": HOJE.isoformat(),
                 "natureza_operacao": "Venda", "cliente": self.cli.pk, "valor_frete": "0", "valor_desconto": "0",
                 "itens-TOTAL_FORMS": "1", "itens-INITIAL_FORMS": "0", "itens-MIN_NUM_FORMS": "0",
                 "itens-MAX_NUM_FORMS": "1000", "itens-0-descricao": "Serviço", "itens-0-quantidade": "2",
                 "itens-0-valor_unitario": "100", "itens-0-aliquota_icms": "10"}
        r = self.client.post(reverse("fiscal:notafiscal_create"), dados)
        self.assertEqual(r.status_code, 302)
        nf = NotaFiscal.objects.get(cliente=self.cli)
        self.assertEqual(nf.valor_total, Decimal("200.00"))
        self.assertEqual(nf.valor_icms, Decimal("20.00"))
        self.client.post(reverse("fiscal:emitir_nota", args=[nf.pk]))
        nf.refresh_from_db()
        self.assertEqual((nf.status, nf.numero), ("emitida", 1))


class WorkflowTest(Base):
    def test_aprovacao_de_pagamento(self):
        cp = ContaPagar.objects.create(fornecedor=self.forn, descricao="Compra", valor=Decimal("500"), vencimento=HOJE)
        fin = self.usuario("fin2", "Financeiro")
        rh = self.usuario("rh2", "RH")
        self.client.force_login(fin)
        self.client.post(reverse("workflow:iniciar_aprovacao_pagar", args=[cp.pk]))
        inst = Instancia.objects.get()
        self.assertEqual(inst.etapa_atual.ordem, 1)
        self.assertIn(inst, Instancia.objects.pendentes_para(fin))
        self.assertNotIn(inst, Instancia.objects.pendentes_para(rh))
        # RH não pode aprovar
        self.client.force_login(rh)
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "aprovar"})
        inst.refresh_from_db()
        self.assertEqual(inst.etapa_atual.ordem, 1)
        # financeiro aprova, diretoria (admin) aprova
        self.client.force_login(fin)
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "aprovar", "comentario": "ok"})
        self.client.force_login(self.admin)
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "aprovar"})
        inst.refresh_from_db()
        cp.refresh_from_db()
        self.assertEqual(inst.status, "concluido")
        self.assertTrue(cp.aprovado)
        self.assertEqual(inst.historico.count(), 3)

    def test_rejeicao_exige_comentario(self):
        proc = Processo.objects.get(codigo="aprovacao-pagamento")
        inst = proc.iniciar("Teste", self.admin)
        self.client.force_login(self.admin)
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "rejeitar"})
        inst.refresh_from_db()
        self.assertEqual(inst.status, "andamento")
        self.client.post(reverse("workflow:agir", args=[inst.pk]), {"acao": "rejeitar", "comentario": "sem NF"})
        inst.refresh_from_db()
        self.assertEqual(inst.status, "rejeitado")


class GedTest(Base):
    def test_upload_versao_e_acesso_restrito(self):
        pasta_rh = Pasta.objects.get(nome="Recursos Humanos")
        self.client.force_login(self.admin)
        r = self.client.post(reverse("ged:documento_create"), {
            "pasta": pasta_rh.pk, "titulo": "Contrato de trabalho", "tipo": "rh",
            "arquivo": SimpleUploadedFile("contrato.txt", b"conteudo v1")})
        self.assertEqual(r.status_code, 302)
        doc = Documento.objects.get()
        self.assertEqual(doc.num_versao, 1)
        self.client.post(reverse("ged:nova_versao", args=[doc.pk]),
                         {"arquivo": SimpleUploadedFile("contrato.txt", b"conteudo v2"), "comentario": "revisado"})
        self.assertEqual(doc.num_versao, 2)
        v = doc.versao_atual
        r = self.client.get(reverse("ged:download", args=[v.pk]))
        self.assertEqual(b"".join(r.streaming_content), b"conteudo v2")
        # financeiro não pertence ao grupo RH: não vê a pasta
        fin = self.usuario("fin3", "Financeiro")
        self.client.force_login(fin)
        self.assertEqual(self.client.get(reverse("ged:download", args=[v.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("ged:documento_detail", args=[doc.pk])).status_code, 404)
        rh = self.usuario("rh3", "RH")
        self.client.force_login(rh)
        self.assertEqual(self.client.get(reverse("ged:documento_detail", args=[doc.pk])).status_code, 200)


class TelasTest(TestCase):
    """Abre todas as telas com dados de demonstração para garantir que nada quebra."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("root", "r@r.com", "x")

    def test_todas_as_telas_admin(self):
        self.client.force_login(self.admin)
        urls = ["/", "/financeiro/fluxo-caixa/", "/financeiro/transferencia/", "/fiscal/importar-xml/",
                "/fiscal/apuracao/", "/workflow/tarefas/", "/workflow/solicitacao/nova/", "/banco-de-dados/",
                "/cobranca/regua/executar/", "/admin/", "/senha/"]
        for c in REGISTRO:
            urls.append(reverse(c.url_name("list")))
            urls.append(reverse(c.url_name("list")) + "?export=csv")
            if c.can_create:
                urls.append(reverse(c.url_name("create")))
            obj = c.model.objects.first()
            if obj:
                urls.append(reverse(c.url_name("detail"), args=[obj.pk]))
                if c.can_edit:
                    urls.append(reverse(c.url_name("update"), args=[obj.pk]))
                urls.append(f"/admin/{c.app()}/{c.nome()}/")
        for url in urls:
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, url)

    def test_filtros_e_busca(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("financeiro:contapagar_list"),
                            {"q": "Aluguel", "status": "aberto", "vencimento__gte": HOJE.isoformat(), "o": "-vencimento"})
        self.assertEqual(r.status_code, 200)

    def test_perfil_limitado(self):
        rh = User.objects.get(username="rh")
        self.client.force_login(rh)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get(reverse("core:colaborador_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("financeiro:contapagar_list")).status_code, 403)
        self.assertEqual(self.client.get("/banco-de-dados/").status_code, 302)
        menu = self.client.get("/").context["menu_erp"]
        self.assertNotIn("Financeiro", [g["nome"] for g in menu])

    def test_login_obrigatorio(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/login/").status_code, 200)
