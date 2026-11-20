import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import Colaborador, Departamento, Empresa
from apps.estoque import models as est
from apps.uniformes.models import (ConjuntoUniforme, EntregaUniforme, ItemUniforme, ModeloUniforme, PecaConjunto,
                                   TamanhoColaborador, tamanhos_de)


def png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(b, "PNG")
    return SimpleUploadedFile("polo.png", b.getvalue(), content_type="image/png")


class Base(TestCase):
    def setUp(self):
        self.emp = Empresa.objects.create(razao_social="E", cnpj="11222333000181")
        self.c = Colaborador.objects.create(empresa=self.emp, nome="Ana", cpf="52998224725", tipo_contrato="clt")
        self.polo = ModeloUniforme.objects.create(codigo="polo-az", nome="Polo azul", numeracao="letras",
                                                  vida_util_meses=6, estoque_minimo=5)
        self.polo.sincronizar_itens()
        self.armazem = est.Armazem.principal()

    def item(self, t):
        return ItemUniforme.objects.get(modelo=self.polo, tamanho=t)

    def estoque(self, t, qtd):
        est.entrada(self.item(t).produto, qtd, 20, self.armazem, "ajuste")


class CatalogoTest(Base):
    def test_gera_um_produto_por_tamanho(self):
        self.assertEqual(self.polo.itens.count(), len(tamanhos_de("letras")))
        self.assertEqual(self.item("M").produto.codigo, "UNI-POLO-AZ-M")
        self.assertEqual(self.item("M").produto.estoque_minimo, 5)

    def test_sincronizar_nao_duplica(self):
        self.assertEqual(self.polo.sincronizar_itens(), 0)

    def test_numeracoes(self):
        self.assertEqual(tamanhos_de("calca")[0], "36")
        self.assertEqual(tamanhos_de("calcado")[-1], "48")
        self.assertEqual(tamanhos_de("unico"), ["Único"])

    def test_disponibilidade_e_situacao(self):
        self.estoque("M", 10)
        self.estoque("G", 2)
        p = {x["item"].tamanho: x for x in self.polo.painel()}
        self.assertEqual(p["M"]["disponivel"], 10)
        self.assertEqual(p["M"]["situacao"], "ok")
        self.assertEqual(p["G"]["situacao"], "baixo")
        self.assertEqual(p["P"]["situacao"], "sem")
        self.assertEqual(self.polo.situacao_geral, "baixo")
        self.assertEqual(self.polo.total_em_estoque, 12)

    def test_codigo_normalizado(self):
        m = ModeloUniforme(codigo=" cal ca ", nome="x")
        m.clean()
        self.assertEqual(m.codigo, "CAL-CA")


class EntregaTest(Base):
    def test_entrega_baixa_estoque_e_define_troca(self):
        self.estoque("M", 10)
        e = EntregaUniforme.registrar(self.c, [(self.item("M"), 3)])
        self.assertEqual(self.item("M").disponivel, 7)
        it = e.itens.get()
        self.assertEqual(it.quantidade, 3)
        self.assertIsNotNone(it.trocar_ate)

    def test_sem_estoque_nao_entrega_nada(self):
        self.estoque("M", 1)
        self.estoque("G", 5)
        with self.assertRaises(ValidationError):
            EntregaUniforme.registrar(self.c, [(self.item("G"), 1), (self.item("M"), 2)])
        self.assertEqual(self.item("G").disponivel, 5)
        self.assertEqual(EntregaUniforme.objects.count(), 0)

    def test_sem_linhas(self):
        with self.assertRaises(ValidationError):
            EntregaUniforme.registrar(self.c, [(self.item("M"), 0)])

    def test_desligado(self):
        self.c.status = "desligado"
        self.c.save()
        self.estoque("M", 2)
        with self.assertRaises(ValidationError):
            EntregaUniforme.registrar(self.c, [(self.item("M"), 1)])

    def test_estorno_devolve_estoque_uma_vez(self):
        self.estoque("M", 10)
        e = EntregaUniforme.registrar(self.c, [(self.item("M"), 4)])
        e.estornar()
        self.assertEqual(self.item("M").disponivel, 10)
        self.assertEqual(e.status, "estornada")
        with self.assertRaises(ValidationError):
            e.estornar()


class ConjuntoTest(Base):
    def test_cobertura_por_tamanho(self):
        dep = Departamento.objects.create(empresa=self.emp, nome="Produção")
        self.c.departamento = dep
        self.c.save()
        b = Colaborador.objects.create(empresa=self.emp, nome="Bia", cpf="11144477735", tipo_contrato="clt", departamento=dep)
        Colaborador.objects.create(empresa=self.emp, nome="Fora", cpf="39053344705", tipo_contrato="clt")
        for col in (self.c, b):
            TamanhoColaborador.objects.create(colaborador=col, numeracao="letras", tamanho="M")
        self.estoque("M", 3)
        k = ConjuntoUniforme.objects.create(nome="Produção", departamento=dep)
        PecaConjunto.objects.create(conjunto=k, modelo=self.polo, quantidade=2)
        cob = k.cobertura()[0]
        m = next(l for l in cob["linhas"] if l["item"].tamanho == "M")
        self.assertEqual(m["precisam"], 4)
        self.assertEqual(m["falta"], 1)
        self.assertEqual(cob["falta_total"], 1)
        self.assertEqual(cob["sem_tamanho"], 0)

    def test_tamanho_invalido(self):
        t = TamanhoColaborador(colaborador=self.c, numeracao="letras", tamanho="42")
        with self.assertRaises(ValidationError):
            t.clean()


class TelasTest(Base):
    def setUp(self):
        super().setUp()
        self.u = get_user_model().objects.create_superuser("adm", "a@a.com", "x")
        self.client.force_login(self.u)

    def test_telas_abrem(self):
        for nome in ("catalogo", "entregar_avulso"):
            self.assertEqual(self.client.get(reverse(f"uniformes:{nome}")).status_code, 200, nome)
        for c in ("modelouniforme", "conjuntouniforme", "tamanhocolaborador", "entregauniforme"):
            self.assertEqual(self.client.get(reverse(f"uniformes:{c}_list")).status_code, 200, c)
        self.assertEqual(self.client.get(reverse("uniformes:modelouniforme_detail", args=[self.polo.pk])).status_code, 200)

    def test_cadastro_com_imagem_gera_tamanhos(self):
        r = self.client.post(reverse("uniformes:modelouniforme_create"), {
            "codigo": "cal-1", "nome": "Calça", "categoria": "calca", "genero": "unissex", "numeracao": "calca",
            "estoque_minimo": "2", "ativo": "on", "imagem": png()})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        m = ModeloUniforme.objects.get(codigo="CAL-1")
        self.assertEqual(m.itens.count(), len(tamanhos_de("calca")))
        self.assertEqual(self.client.get(reverse("uniformes:imagem", args=["modelo", m.pk])).status_code, 200)

    def test_imagem_exige_login(self):
        self.client.logout()
        r = self.client.get(reverse("uniformes:imagem", args=["modelo", self.polo.pk]))
        self.assertEqual(r.status_code, 302)

    def test_arquivo_que_nao_e_imagem_recusado(self):
        r = self.client.post(reverse("uniformes:modelouniforme_create"), {
            "codigo": "x", "nome": "X", "categoria": "camisa", "genero": "unissex", "numeracao": "letras",
            "estoque_minimo": "0", "imagem": SimpleUploadedFile("a.exe", b"MZ", content_type="application/octet-stream")})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ModeloUniforme.objects.filter(codigo="X").exists())

    def test_fluxo_entregar_termo_assinar_estornar(self):
        self.estoque("M", 5)
        TamanhoColaborador.objects.create(colaborador=self.c, numeracao="letras", tamanho="M")
        k = ConjuntoUniforme.objects.create(nome="Kit")
        PecaConjunto.objects.create(conjunto=k, modelo=self.polo, quantidade=2)
        r = self.client.get(reverse("uniformes:entregar", args=[k.pk]) + f"?colaborador={self.c.pk}")
        self.assertContains(r, 'selected')
        r = self.client.post(reverse("uniformes:entregar", args=[k.pk]), {
            "colaborador": self.c.pk, "data": "2026-11-20", "observacoes": "",
            f"item_{self.polo.pk}": self.item("M").pk, f"qtd_{self.polo.pk}": "2"})
        self.assertEqual(r.status_code, 302)
        e = EntregaUniforme.objects.get()
        self.assertEqual(self.item("M").disponivel, 3)
        self.assertEqual(self.client.get(reverse("uniformes:entrega_termo", args=[e.pk])).status_code, 200)
        self.client.post(reverse("uniformes:entrega_assinada", args=[e.pk]))
        e.refresh_from_db()
        self.assertTrue(e.recebido_confirmado)
        self.client.post(reverse("uniformes:entrega_estornar", args=[e.pk]))
        self.assertEqual(self.item("M").disponivel, 5)
        # ações só nos status certos: estornada não mostra mais estornar/assinar
        r = self.client.get(reverse("uniformes:entregauniforme_detail", args=[e.pk]))
        self.assertNotContains(r, reverse("uniformes:entrega_estornar", args=[e.pk]))

    def test_entrega_sem_estoque_mostra_erro(self):
        r = self.client.post(reverse("uniformes:entregar_avulso"), {
            "colaborador": self.c.pk, "data": "2026-11-20", f"item_{self.polo.pk}": self.item("M").pk,
            f"qtd_{self.polo.pk}": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Sem estoque suficiente")

    def test_catalogo_filtra_por_situacao(self):
        self.estoque("M", 10)
        r = self.client.get(reverse("uniformes:catalogo") + "?situacao=sem")
        self.assertNotContains(r, "Polo azul")
        r = self.client.get(reverse("uniformes:catalogo") + "?situacao=baixo")
        self.assertContains(r, "Polo azul")

    def test_sem_permissao(self):
        u = get_user_model().objects.create_user("sem", "s@s.com", "x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("uniformes:catalogo")).status_code, 403)
        self.assertEqual(self.client.post(reverse("uniformes:entrega_estornar", args=[1])).status_code, 403)


class IsolamentoPorEmpresaTest(Base):
    """Usuário só enxerga e mexe em entregas de colaboradores das empresas a que tem acesso."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Permission
        self.outra = Empresa.objects.create(razao_social="Outra", cnpj="45723174000110")
        self.dela = Colaborador.objects.create(empresa=self.outra, nome="Zeca", cpf="39053344705", tipo_contrato="clt")
        self.estoque("M", 5)
        self.entrega_outra = EntregaUniforme.registrar(self.dela, [(self.item("M"), 1)])
        self.u = get_user_model().objects.create_user("op", "o@o.com", "x")
        self.u.user_permissions.set(Permission.objects.filter(content_type__app_label="uniformes"))
        self.emp.usuarios.add(self.u)
        self.outra.usuarios.add(get_user_model().objects.create_user("outro", "x@x.com", "x"))
        self.client.force_login(self.u)

    def test_nao_ve_termo_nem_estorna_de_outra_empresa(self):
        self.assertEqual(self.client.get(reverse("uniformes:entrega_termo", args=[self.entrega_outra.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("uniformes:entrega_estornar", args=[self.entrega_outra.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("uniformes:entrega_assinada", args=[self.entrega_outra.pk])).status_code, 404)
        self.entrega_outra.refresh_from_db()
        self.assertEqual(self.entrega_outra.status, "entregue")

    def test_nao_entrega_a_colaborador_de_outra_empresa(self):
        r = self.client.post(reverse("uniformes:entregar_avulso"), {
            "colaborador": self.dela.pk, "data": "2026-11-20", f"item_{self.polo.pk}": self.item("M").pk,
            f"qtd_{self.polo.pk}": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(EntregaUniforme.objects.filter(colaborador=self.dela).count(), 1)

    def test_a_propria_empresa_continua_funcionando(self):
        r = self.client.post(reverse("uniformes:entregar_avulso"), {
            "colaborador": self.c.pk, "data": "2026-11-20", f"item_{self.polo.pk}": self.item("M").pk,
            f"qtd_{self.polo.pk}": "1"})
        self.assertEqual(r.status_code, 302)
