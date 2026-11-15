import datetime
import io
from decimal import Decimal as D

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from lxml import etree

from apps.core.crud import REGISTRO
from apps.core.models import Colaborador, Empresa, LogAuditoria
from apps.fiscal.models import ConfiguracaoFiscal
from apps.ged.cert_teste import gerar_pfx
from apps.rh import esocial, ferias, folha as folha_svc
from apps.rh.models import (ConfiguracaoESocial, DadosTrabalhistas, EventoESocial, Folha, Jornada, Marcacao, Rubrica,
                            SolicitacaoFerias)


def data(s):
    return datetime.date.fromisoformat(s)


CNPJ = "11222333000181"


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", stdout=io.StringIO())
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "SenhaForte#2026")
        cls.emp = Empresa.objects.create(razao_social="Teste Ltda", cnpj=CNPJ, regime_tributario="simples")

    def colab(self, nome="Fulano", cpf="52998224725", usuario=None, **kw):
        kw.setdefault("salario", D("3000"))
        kw.setdefault("data_admissao", data("2025-01-01"))
        c = Colaborador.objects.create(empresa=self.emp, nome=nome, cpf=cpf, usuario=usuario, matricula=nome[:3], **kw)
        DadosTrabalhistas.objects.create(colaborador=c, jornada=Jornada.objects.first(), pis="12345678901", sexo="M",
                                         estado_civil="1", grau_instrucao="09", cbo="411010",
                                         codigo_municipio="3550308")
        Colaborador.objects.filter(pk=c.pk).update(data_nascimento=data("1990-05-10"), cep="01001-000",
                                                   logradouro="Rua A", numero="10", bairro="Centro", uf="SP")
        c.refresh_from_db()
        return c


class TelasTest(Base):
    def test_todas_as_listas_e_formularios_abrem(self):
        self.client.force_login(self.admin)
        telas = [c for c in REGISTRO if c.app() == "rh"]
        self.assertGreaterEqual(len(telas), 17)
        for c in telas:
            self.assertEqual(self.client.get(reverse(c.url_name("list"))).status_code, 200, c.nome())
            if c.can_create:
                self.assertEqual(self.client.get(reverse(c.url_name("create"))).status_code, 200, c.nome())

    def test_menu_rh_para_o_grupo_rh_e_usuario_comum(self):
        rh = User.objects.create_user("rh", password="x")
        rh.groups.add(Group.objects.get(name="RH"))
        self.client.force_login(rh)
        html = self.client.get("/").content.decode()
        self.assertIn("Folhas de pagamento", html)
        self.assertIn("Bater ponto", html)
        comum = User.objects.create_user("comum", password="x")
        self.client.force_login(comum)
        html = self.client.get("/").content.decode()
        self.assertIn("Bater ponto", html)
        self.assertNotIn("Folhas de pagamento", html)
        self.assertEqual(self.client.get(reverse("rh:folha_list")).status_code, 403)

    def test_bater_ponto_e_espelho_proprio_e_alheio(self):
        u1, u2 = User.objects.create_user("u1", password="x"), User.objects.create_user("u2", password="x")
        c1 = self.colab("Um", usuario=u1)
        c2 = self.colab("Dois", cpf="11144477735", usuario=u2)
        self.client.force_login(u1)
        self.assertContains(self.client.get(reverse("rh:bater_ponto")), "Registrar ponto")
        self.client.post(reverse("rh:bater_ponto"), REMOTE_ADDR="10.0.0.9")
        m = Marcacao.objects.get(colaborador=c1)
        self.assertEqual((m.ip, m.origem), ("10.0.0.9", "web"))
        self.assertEqual(self.client.get(reverse("rh:meu_espelho")).status_code, 200)
        self.assertEqual(self.client.get(reverse("rh:espelho", args=[c2.pk])).status_code, 403)
        r = self.client.get(reverse("rh:meu_espelho") + "?export=csv")
        self.assertIn("text/csv", r["Content-Type"])
        self.assertTrue(LogAuditoria.objects.filter(acao="exportar", descricao__contains="espelho de ponto").exists())
        # a lista de marcações só mostra as próprias a quem não gerencia o ponto
        u1.user_permissions.add(*__import__("django.contrib.auth.models", fromlist=["Permission"]).Permission.objects
                                .filter(codename="view_marcacao"))
        Marcacao.objects.create(colaborador=c2, origem="importado")
        html = self.client.get(reverse("rh:marcacao_list")).content.decode()
        self.assertIn("Um", html)
        self.assertNotIn("Dois", html)

    def test_sem_vinculo_com_colaborador_avisa(self):
        u = User.objects.create_user("semcolab", password="x")
        self.client.force_login(u)
        r = self.client.post(reverse("rh:bater_ponto"), follow=True)
        self.assertContains(r, "não está vinculado")

    def test_ferias_pelo_usuario_e_aprovacao_pelo_gestor(self):
        ug, uc = User.objects.create_user("gestor", password="x"), User.objects.create_user("colab", password="x")
        gestor = self.colab("Chefe", cpf="39053344705", usuario=ug, salario=D("8000"))
        c = self.colab("Func", usuario=uc, gestor=gestor)
        ferias.gerar_periodos(c, ate=data("2026-03-01"))
        per = c.periodos_ferias.get(inicio=data("2025-01-01"))
        self.client.force_login(uc)
        r = self.client.post(reverse("rh:solicitar_ferias"), {"periodo": per.pk, "inicio": "2026-02-02", "dias": 30,
                                                              "abono_dias": 0})
        self.assertEqual(r.status_code, 302)
        s = SolicitacaoFerias.objects.get()
        self.assertEqual(s.status, "solicitada")
        # o próprio colaborador não aprova
        self.assertEqual(self.client.post(reverse("rh:ferias_aprovar", args=[s.pk])).status_code, 403)
        self.client.force_login(ug)
        self.client.post(reverse("rh:ferias_aprovar", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual((s.status, s.decidido_por), ("aprovada", ug))
        self.client.force_login(uc)
        self.assertContains(self.client.get(reverse("rh:minhas_ferias")), "Aprovada")

    def test_folha_pelas_telas_e_acesso_ao_holerite(self):
        u = User.objects.create_user("func", password="x")
        c = self.colab("Func", usuario=u)
        outro = User.objects.create_user("outro", password="x")
        rh = User.objects.create_user("rh", password="x")
        rh.groups.add(Group.objects.get(name="RH"))
        self.client.force_login(rh)
        f = Folha.objects.create(empresa=self.emp, competencia=data("2026-03-01"))
        self.assertEqual(self.client.post(reverse("rh:folha_calcular", args=[f.pk])).status_code, 302)
        f.refresh_from_db()
        self.assertEqual(f.status, "calculada")
        self.assertContains(self.client.get(reverse("rh:folha_detail", args=[f.pk])), "Func")
        h = f.holerites.get()
        self.assertEqual(self.client.get(reverse("rh:holerite_ver", args=[h.pk])).status_code, 200)
        self.assertTrue(LogAuditoria.objects.filter(acao="ler", modelo="rh.holerite").exists())
        # o colaborador só vê depois de a folha fechar
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("rh:holerite_ver", args=[h.pk])).status_code, 403)
        self.client.force_login(rh)
        self.client.post(reverse("rh:folha_fechar", args=[f.pk]))
        self.client.force_login(u)
        self.assertContains(self.client.get(reverse("rh:holerite_ver", args=[h.pk])), "2.751,40")
        self.assertContains(self.client.get(reverse("rh:meus_holerites")), "2.751,40")
        self.client.force_login(outro)
        self.assertEqual(self.client.get(reverse("rh:holerite_ver", args=[h.pk])).status_code, 403)

    def test_rescisao_pelas_telas(self):
        rh = User.objects.create_user("rh", password="x")
        rh.groups.add(Group.objects.get(name="RH"))
        c = self.colab("Saindo")
        self.client.force_login(rh)
        self.client.post(reverse("rh:rescisao_create"), {
            "empresa": self.emp.pk, "colaborador": c.pk, "data_desligamento": "2026-03-15", "motivo": "sem_justa_causa",
            "aviso": "indenizado", "saldo_fgts": "5000"})
        from apps.rh.models import Rescisao

        r = Rescisao.objects.get()
        self.client.post(reverse("rh:rescisao_calcular", args=[r.pk]))
        r.refresh_from_db()
        self.assertGreater(r.total_liquido, 0)
        self.assertContains(self.client.get(reverse("rh:rescisao_detail", args=[r.pk])), "Memória de cálculo")
        self.client.post(reverse("rh:rescisao_efetivar", args=[r.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, "desligado")


class ESocialTest(Base):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.pfx = gerar_pfx("GUARA TESTE", cnpj=CNPJ, senha="s3nha")

    def config(self):
        ConfiguracaoESocial.objects.create(empresa=self.emp, contato_nome="Maria Contato", contato_cpf="52998224725",
                                           contato_email="m@x.com")

    def test_s1000_exige_contato_e_gera_xml(self):
        with self.assertRaises(Exception) as cm:
            esocial.gerar_s1000(self.emp)
        self.assertIn("Configuração do eSocial", str(cm.exception))
        self.config()
        ev = esocial.gerar_s1000(self.emp)
        raiz = etree.fromstring(ev.xml.encode())
        ns = {"e": raiz.nsmap[None]}
        self.assertTrue(raiz.nsmap[None].endswith("evtInfoEmpregador/v_S_01_03_00"))
        self.assertEqual(raiz.xpath("//e:ideEmpregador/e:nrInsc", namespaces=ns)[0].text, "11222333")   # raiz do CNPJ
        self.assertEqual(raiz.xpath("//e:classTrib", namespaces=ns)[0].text, "01")                      # Simples
        self.assertEqual(raiz.xpath("//e:tpAmb", namespaces=ns)[0].text, "2")                           # restrita
        self.assertEqual(len(ev.identificador), 36)
        self.assertTrue(ev.identificador.startswith("ID1" + CNPJ))

    def test_s1010_pede_natureza_das_rubricas(self):
        with self.assertRaises(Exception) as cm:
            esocial.gerar_s1010(self.emp)
        self.assertIn("Natureza eSocial", str(cm.exception))
        Rubrica.objects.filter(natureza_esocial="").update(natureza_esocial="1000")
        evs = esocial.gerar_s1010(self.emp)
        self.assertEqual(len(evs), Rubrica.objects.count())

    def test_s2200_pendencias_e_conteudo(self):
        c = Colaborador.objects.create(empresa=self.emp, nome="Sem Dados", cpf="52998224725", salario=D("3000"),
                                       data_admissao=data("2026-01-02"))
        with self.assertRaises(Exception) as cm:
            esocial.gerar_s2200(c)
        self.assertIn("dados trabalhistas", str(cm.exception))
        c2 = self.colab("Completo", cpf="11144477735")
        from apps.rh.models import Dependente

        Dependente.objects.create(colaborador=c2, nome="Filha", data_nascimento=data("2020-01-01"))
        ev = esocial.gerar_s2200(c2)
        raiz = etree.fromstring(ev.xml.encode())
        ns = {"e": raiz.nsmap[None]}
        self.assertEqual(raiz.xpath("//e:cpfTrab", namespaces=ns)[0].text, "11144477735")
        self.assertEqual(raiz.xpath("//e:vrSalFx", namespaces=ns)[0].text, "3000.00")
        self.assertEqual(raiz.xpath("//e:dependente/e:tpDep", namespaces=ns)[0].text, "03")
        self.assertEqual(raiz.xpath("//e:codMunic", namespaces=ns)[0].text, "3550308")

    def test_s1200_s1210_s1299_e_s2299(self):
        self.config()
        self.colab("Func")
        f = Folha.objects.create(empresa=self.emp, competencia=data("2026-03-01"))
        folha_svc.calcular(f)
        h = f.holerites.get()
        ev = esocial.gerar_s1200(f, h)
        ns = {"e": etree.fromstring(ev.xml.encode()).nsmap[None]}
        raiz = etree.fromstring(ev.xml.encode())
        self.assertEqual(raiz.xpath("//e:perApur", namespaces=ns)[0].text, "2026-03")
        rubricas = raiz.xpath("//e:itensRemun/e:codRubr", namespaces=ns)
        self.assertEqual([r.text for r in rubricas], ["0001"])        # INSS/IRRF/FGTS não são rubricas
        p = esocial.gerar_s1210(f, h, data("2026-04-05"))
        raiz = etree.fromstring(p.xml.encode())
        ns = {"e": raiz.nsmap[None]}
        self.assertEqual(raiz.xpath("//e:vrLiq", namespaces=ns)[0].text, "2751.40")
        self.assertEqual(raiz.xpath("//e:dtPgto", namespaces=ns)[0].text, "2026-04-05")
        fech = esocial.gerar_s1299(self.emp, data("2026-03-01"))
        self.assertIn("<evtRemun>S</evtRemun>", fech.xml)
        from apps.rh.models import Rescisao

        r = Rescisao.objects.create(empresa=self.emp, colaborador=h.colaborador, data_desligamento=data("2026-04-10"),
                                    motivo="acordo", aviso="indenizado")
        s = esocial.gerar_s2299(r)
        raiz = etree.fromstring(s.xml.encode())
        self.assertEqual(raiz.xpath("//e:mtvDeslig", namespaces={"e": raiz.nsmap[None]})[0].text, "33")

    def test_assinatura_e_lote(self):
        self.config()
        cfg = ConfiguracaoFiscal.objects.create(empresa=self.emp)
        ev = esocial.gerar_s1000(self.emp)
        with self.assertRaises(Exception):
            esocial.lote_xml([ev])                      # sem assinar
        cfg.guardar_certificado(self.pfx, "s3nha")
        ev = esocial.assinar_evento(EventoESocial.objects.get(pk=ev.pk))
        self.assertEqual(ev.status, "assinado")
        from apps.fiscal import assinatura_xml

        self.assertTrue(assinatura_xml.verificar(ev.xml_assinado.encode()))
        lote = etree.fromstring(esocial.lote_xml([ev]).encode())
        self.assertEqual(lote.xpath("//*[local-name()='envioLoteEventos']/@grupo")[0], "1")
        self.assertEqual(len(lote.xpath("//*[local-name()='evento']")), 1)
        self.assertEqual(len(lote.xpath("//*[local-name()='Signature']")), 1)

    def test_telas_gerar_assinar_baixar_e_retorno(self):
        self.client.force_login(self.admin)
        self.config()
        r = self.client.post(reverse("rh:esocial_gerar"), {"tipo": "S-1000", "empresa": self.emp.pk})
        self.assertEqual(r.status_code, 302)
        ev = EventoESocial.objects.get()
        self.assertContains(self.client.get(reverse("rh:eventoesocial_detail", args=[ev.pk])), "evtInfoEmpregador")
        r = self.client.get(reverse("rh:esocial_baixar", args=[ev.pk]))
        self.assertIn("application/xml", r["Content-Type"])
        self.assertTrue(LogAuditoria.objects.filter(acao="exportar", modelo="rh.eventoesocial").exists())
        self.client.post(reverse("rh:esocial_recibo", args=[ev.pk]),
                         {"status": "aceito", "recibo": "1.2.1234567", "retorno": "ok"})
        ev.refresh_from_db()
        self.assertEqual((ev.status, ev.recibo), ("aceito", "1.2.1234567"))
        self.assertEqual(self.client.get(reverse("rh:esocial_montar_lote")).status_code, 200)
