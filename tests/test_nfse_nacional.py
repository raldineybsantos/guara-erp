import base64
import gzip
import json
import os
import stat
from decimal import Decimal
from unittest import mock

import requests
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from lxml import etree

from apps.core.models import Cliente, Empresa
from apps.fiscal import assinatura_xml, transmissao
from apps.fiscal.emissores import nfse_nacional as nac
from apps.fiscal.models import ConfiguracaoFiscal, ItemNota, NotaFiscal, Produto
from apps.ged.cert_teste import gerar_pfx

CNPJ = "11222333000181"
NS = {"n": nac.NS}
CHAVE = "35503082" + "2" + CNPJ[:0] + "11222333000181" + "000000000000100126110112345678901"[:27]


def pacote(xml: str) -> str:
    return base64.b64encode(gzip.compress(xml.encode())).decode()


def nfse_xml(numero="77"):
    return f'<NFSe xmlns="{nac.NS}"><infNFSe><nNFSe>{numero}</nNFSe></infNFSe></NFSe>'


class Resp:
    def __init__(self, status, corpo=None):
        self.status_code, self._corpo = status, corpo
        self.text = json.dumps(corpo) if corpo is not None else ""

    def json(self):
        if self._corpo is None:
            raise ValueError("sem corpo")
        return self._corpo


class FalsaSessao:
    """Troca a sessão HTTPS: registra as chamadas e responde com o que o teste preparou."""

    def __init__(self, respostas):
        self.respostas, self.chamadas, self.headers = list(respostas), [], {}
        self.cert = self.verify = None
        self.arquivos_existiam = None

    def _responder(self, metodo, url, **kw):
        self.chamadas.append((metodo, url, kw))
        if metodo == "POST":
            self.arquivos_existiam = [os.path.exists(c) for c in self.cert]
            self.modos = [stat.S_IMODE(os.stat(c).st_mode) for c in self.cert]
        item = self.respostas.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, **kw):
        return self._responder("POST", url, **kw)

    def get(self, url, **kw):
        return self._responder("GET", url, **kw)


class NfseNacionalTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.emp = Empresa.objects.create(razao_social="Guará Serviços Ltda", cnpj=CNPJ, uf="SP", matriz=True,
                                         codigo_municipio="3550308", inscricao_municipal="12345678",
                                         regime_tributario="simples")
        cls.cli = Cliente.objects.create(nome="Cliente Tomador", documento="11444777000161", cep="01310-100",
                                         logradouro="Av. Paulista", numero="1000", bairro="Bela Vista",
                                         codigo_municipio="3550308", email="c@c.com")
        cls.servico = Produto.objects.create(codigo="S1", descricao="Consultoria em TI", tipo="servico",
                                             codigo_tributacao_nacional="01.07.01", aliquota_iss=2, preco=500)
        cls.pfx = gerar_pfx("GUARA SERVICOS", cnpj=CNPJ, senha="s3nha")
        cls.config = ConfiguracaoFiscal.objects.create(empresa=cls.emp, emissor_nfse="nacional")
        cls.config.guardar_certificado(cls.pfx, "s3nha")

    def nota(self, **kw):
        n = NotaFiscal.objects.create(empresa=self.emp, tipo="saida", modelo="nfse", cliente=self.cli,
                                      natureza_operacao="Prestação de serviço", **kw)
        ItemNota.objects.create(nota=n, produto=self.servico, descricao="Consultoria em TI", quantidade=2,
                                valor_unitario=500)
        n.recalcular()
        n.emitir()
        return n

    def sessao(self, *respostas):
        s = FalsaSessao(respostas)
        patcher = mock.patch.object(nac.requests, "Session", lambda: s)
        patcher.start()
        self.addCleanup(patcher.stop)
        return s

    # ------------------------------------------------------------------------------------- DPS
    def test_dps_estrutura_id_e_assinatura(self):
        n = self.nota()
        xml, ident = nac.montar_dps(n, self.config)
        self.assertEqual(ident, f"DPS35503082{CNPJ}00001{n.numero:015d}")
        self.assertEqual(len(ident), 45)
        raiz = etree.fromstring(xml)
        inf = raiz.find("n:infDPS", NS)
        self.assertEqual(inf.get("Id"), ident)
        self.assertEqual([etree.QName(e).localname for e in inf],
                         ["tpAmb", "dhEmi", "verAplic", "serie", "nDPS", "dCompet", "tpEmit", "cLocEmi", "prest",
                          "toma", "serv", "valores"])
        t = lambda caminho: inf.findtext(caminho, namespaces=NS)  # noqa: E731
        self.assertEqual((t("n:tpAmb"), t("n:serie"), t("n:cLocEmi")), ("2", "00001", "3550308"))
        self.assertEqual((t("n:prest/n:CNPJ"), t("n:prest/n:IM")), (CNPJ, "12345678"))
        self.assertEqual((t("n:prest/n:regTrib/n:opSimpNac"), t("n:prest/n:regTrib/n:regApTribSN")), ("3", "1"))
        self.assertEqual((t("n:toma/n:CNPJ"), t("n:toma/n:end/n:endNac/n:cMun"), t("n:toma/n:end/n:endNac/n:CEP")),
                         ("11444777000161", "3550308", "01310100"))
        self.assertEqual((t("n:serv/n:cServ/n:cTribNac"), t("n:valores/n:vServPrest/n:vServ")), ("010701", "1000.00"))
        self.assertEqual(t("n:valores/n:trib/n:tribMun/n:tpRetISSQN"), "1")
        self.assertIsNone(inf.find("n:valores/n:trib/n:tribMun/n:pAliq", NS))   # Simples: a alíquota é do DAS
        chave_priv, cert = self.config.abrir_certificado()
        assinado = assinatura_xml.assinar(xml, ident, chave_priv, cert)
        self.assertTrue(assinatura_xml.verificar(assinado))
        self.assertEqual(etree.fromstring(assinado)[1].tag, "{http://www.w3.org/2000/09/xmldsig#}Signature")

    def test_regimes_e_aliquota_de_iss(self):
        self.emp.regime_tributario = "presumido"
        n = self.nota()
        raiz = etree.fromstring(nac.montar_dps(n, self.config)[0])
        self.assertEqual(raiz.findtext(".//n:opSimpNac", namespaces=NS), "1")
        self.assertIsNone(raiz.find(".//n:regApTribSN", NS))
        self.assertEqual(raiz.findtext(".//n:tribMun/n:pAliq", namespaces=NS), "2.00")
        self.emp.regime_tributario = "mei"
        raiz = etree.fromstring(nac.montar_dps(n, self.config)[0])
        self.assertEqual(raiz.findtext(".//n:opSimpNac", namespaces=NS), "2")

    def test_um_servico_por_nota(self):
        outro = Produto.objects.create(codigo="S2", descricao="Treinamento", tipo="servico",
                                       codigo_tributacao_nacional="080101", aliquota_iss=2)
        n = self.nota()
        ItemNota.objects.create(nota=n, produto=outro, descricao="Treinamento", quantidade=1, valor_unitario=300)
        faltas = nac.pendencias(n, self.config)
        self.assertTrue(any("serviços diferentes" in f for f in faltas), faltas)
        n.itens.filter(produto=outro).update(produto=self.servico)   # mesmo serviço: soma numa DPS só
        self.assertEqual(nac.pendencias(n, self.config), [])
        xml = etree.fromstring(nac.montar_dps(n, self.config)[0])
        self.assertEqual(xml.findtext(".//n:vServ", namespaces=NS), "1300.00")

    def test_pendencias_listam_o_que_falta(self):
        self.servico.codigo_tributacao_nacional = ""
        self.servico.save()
        self.emp.inscricao_municipal, self.emp.codigo_municipio = "", ""
        n = self.nota()
        n.cliente.documento = ""
        faltas = " | ".join(nac.pendencias(n, self.config))
        for trecho in ("inscrição municipal", "código IBGE", "CPF/CNPJ do cliente", "código de tributação nacional"):
            self.assertIn(trecho, faltas)
        r = nac.NfseNacional().autorizar(n, self.config)
        self.assertEqual((r.ok, r.codigo), (False, "GUARA-001"))

    # ------------------------------------------------------------------------------ transmissão
    def test_autorizacao_envia_dps_assinada_com_mtls(self):
        s = self.sessao(Resp(201, {"chaveAcesso": "3" * 50, "nfseXmlGZipB64": pacote(nfse_xml("77"))}))
        n = self.nota()
        t = transmissao.transmitir(n)
        self.assertEqual((t.resultado, t.protocolo), ("autorizado", "77"))
        metodo, url, kw = s.chamadas[0]
        self.assertEqual((metodo, url), ("POST", "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse"))
        corpo = json.loads(kw["data"])
        enviado = gzip.decompress(base64.b64decode(corpo["dpsXmlGZipB64"]))
        self.assertTrue(assinatura_xml.verificar(enviado))
        self.assertEqual(s.arquivos_existiam, [True, True])    # certificado e chave existiam durante a chamada
        self.assertEqual(s.modos, [0o600, 0o600])
        self.assertFalse(any(os.path.exists(c) for c in s.cert))   # e foram apagados depois
        n.refresh_from_db()
        self.assertEqual((n.situacao_fiscal, n.chave_acesso, n.protocolo), ("autorizada", "3" * 50, "77"))
        self.assertIn("<nNFSe>77</nNFSe>", n.xml.read().decode())

    def test_rejeicao_traz_codigo_e_texto_do_fisco(self):
        self.sessao(Resp(400, {"erros": [{"Codigo": "E0160", "Descricao": "Opção do Simples não confere",
                                           "Complemento": "prest/regTrib"}]}))
        n = self.nota()
        t = transmissao.transmitir(n)
        self.assertEqual((t.resultado, t.codigo), ("rejeitado", "E0160"))
        self.assertIn("Opção do Simples", t.mensagem)
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "rejeitada")

    def test_certificado_recusado_403(self):
        self.sessao(Resp(403, {}))
        t = transmissao.transmitir(self.nota())
        self.assertEqual((t.resultado, t.codigo), ("rejeitado", "HTTP403"))

    def test_queda_de_conexao_recupera_pela_consulta_da_dps(self):
        s = self.sessao(requests.ConnectionError("cortou"), Resp(200, {"chaveAcesso": "4" * 50}),
                        Resp(200, {"nfseXmlGZipB64": pacote(nfse_xml("9"))}))
        n = self.nota()
        t = transmissao.transmitir(n)
        self.assertEqual((t.resultado, t.protocolo), ("autorizado", "9"))
        self.assertTrue(s.chamadas[1][1].endswith(f"/dps/DPS35503082{CNPJ}00001{n.numero:015d}"))
        n.refresh_from_db()
        self.assertEqual(n.chave_acesso, "4" * 50)

    def test_sem_resposta_nao_decide_a_nota(self):
        self.sessao(requests.Timeout("lento"), Resp(404, {}))
        n = self.nota()
        t = transmissao.transmitir(n)
        self.assertEqual(t.resultado, "erro")
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "")    # pode tentar de novo
        self.sessao(Resp(201, {"chaveAcesso": "5" * 50, "nfseXmlGZipB64": pacote(nfse_xml())}))
        self.assertEqual(transmissao.transmitir(n).resultado, "autorizado")

    def test_dps_repetida_traz_a_nota_existente(self):
        self.sessao(Resp(400, {"erros": [{"Codigo": "E0014", "Descricao": "DPS já existe"}]}),
                    Resp(200, {"chaveAcesso": "6" * 50}), Resp(200, {"nfseXmlGZipB64": pacote(nfse_xml("5"))}))
        t = transmissao.transmitir(self.nota())
        self.assertEqual((t.resultado, t.protocolo), ("autorizado", "5"))

    def test_erro_500_nao_marca_rejeitada(self):
        self.sessao(Resp(500, {"erros": [{"Codigo": "RNG9999", "Descricao": "Erro não catalogado."}]}))
        n = self.nota()
        self.assertEqual(transmissao.transmitir(n).resultado, "erro")
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "")

    # ------------------------------------------------------------------------------ cancelamento
    def autorizar(self):
        self.sessao(Resp(201, {"chaveAcesso": "7" * 50, "nfseXmlGZipB64": pacote(nfse_xml())}))
        n = self.nota()
        transmissao.transmitir(n)
        n.refresh_from_db()
        return n

    def test_cancelamento_envia_evento_101101_assinado(self):
        n = self.autorizar()
        s = self.sessao(Resp(200, {"eventoXmlGZipB64": pacote("<evento/>")}))
        t = transmissao.cancelar(n, "Serviço contratado por engano pelo cliente")
        self.assertEqual(t.resultado, "autorizado")
        metodo, url, kw = s.chamadas[0]
        self.assertTrue(url.endswith(f"/nfse/{'7' * 50}/eventos"))
        enviado = gzip.decompress(base64.b64decode(json.loads(kw["data"])["pedidoRegistroEventoXmlGZipB64"]))
        self.assertTrue(assinatura_xml.verificar(enviado))
        raiz = etree.fromstring(enviado)
        inf = raiz.find("n:infPedReg", NS)
        self.assertEqual(inf.get("Id"), f"PRE{'7' * 50}101101")
        self.assertEqual((inf.findtext("n:CNPJAutor", namespaces=NS), inf.findtext("n:chNFSe", namespaces=NS)),
                         (CNPJ, "7" * 50))
        self.assertEqual(inf.findtext("n:e101101/n:xDesc", namespaces=NS), "Cancelamento de NFS-e")
        self.assertIn("engano", inf.findtext("n:e101101/n:xMotivo", namespaces=NS))
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "cancelada")

    def test_cancelamento_recusado_mantem_autorizada(self):
        n = self.autorizar()
        self.sessao(Resp(400, {"erros": [{"Codigo": "E0822", "Descricao": "O prazo para o cancelamento expirou"}]}))
        t = transmissao.cancelar(n, "Serviço contratado por engano pelo cliente")
        self.assertEqual((t.resultado, t.codigo), ("rejeitado", "E0822"))
        n.refresh_from_db()
        self.assertEqual(n.situacao_fiscal, "autorizada")

    # ------------------------------------------------------------------------------- travas
    def test_producao_fica_travada_ate_liberar(self):
        self.config.ambiente = "producao"
        self.config.save()
        n = self.nota()
        with self.assertRaisesMessage(ValidationError, "travada"):
            transmissao.transmitir(n)
        s = self.sessao(Resp(201, {"chaveAcesso": "8" * 50, "nfseXmlGZipB64": pacote(nfse_xml())}))
        with override_settings(FISCAL_NFSE_NACIONAL_PRODUCAO=True):
            self.assertEqual(transmissao.transmitir(n).resultado, "autorizado")
        self.assertTrue(s.chamadas[0][1].startswith("https://sefin.nfse.gov.br/"))
        enviado = gzip.decompress(base64.b64decode(json.loads(s.chamadas[0][2]["data"])["dpsXmlGZipB64"]))
        self.assertEqual(etree.fromstring(enviado).findtext(".//n:tpAmb", namespaces=NS), "1")

    def test_certificado_da_matriz_nao_serve(self):
        self.config.usar_certificado_matriz = True
        faltas = " ".join(nac.pendencias(self.nota(), self.config))
        self.assertIn("certificado do próprio CNPJ", faltas)

    # ------------------------------------------------------------------------------ diagnóstico
    def test_diagnostico_e_comando(self):
        from io import StringIO

        from django.core.management import CommandError

        linhas = nac.diagnosticar(self.config)
        self.assertFalse([t for n, t in linhas if n == "erro"], linhas)
        self.emp.inscricao_municipal = ""
        self.emp.save()
        saida = StringIO()
        with self.assertRaises(CommandError):
            call_command("nfse_nacional_diagnostico", stdout=saida)
        self.assertIn("inscrição municipal FALTA", saida.getvalue())
        self.emp.inscricao_municipal = "12345678"
        self.emp.save()
        s = self.sessao(Resp(404, {}))
        call_command("nfse_nacional_diagnostico", "--conectar", stdout=StringIO())
        self.assertEqual(s.chamadas[0][0], "GET")
        self.sessao(Resp(403, {}))
        with self.assertRaises(CommandError):
            call_command("nfse_nacional_diagnostico", "--conectar", stdout=StringIO())

    def test_codigo_de_tributacao_aceita_pontos(self):
        p = Produto(codigo="S9", descricao="X", tipo="servico", codigo_tributacao_nacional="01.07.01",
                    codigo_nbs="1.1201.19.00")
        p.full_clean()
        self.assertEqual((p.codigo_tributacao_nacional, p.codigo_nbs), ("010701", "112011900"))
        p.codigo_tributacao_nacional = "0107"
        with self.assertRaises(ValidationError):
            p.full_clean()
