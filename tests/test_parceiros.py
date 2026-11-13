"""Parceiro de negócios único: um CPF/CNPJ, vários papéis (cliente, fornecedor)."""

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.core.models import Cliente, Fornecedor, ParceiroNegocio
from apps.financeiro.models import ContaPagar
from apps.fiscal.importador import importar_nfe

from .test_erp import XML_NFE

CNPJ = "11222333000181"  # o mesmo emitente do XML_NFE


class ParceiroUnicoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_erp", verbosity=0)
        cls.admin = User.objects.create_superuser("admin", "a@a.com", "x")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_cliente_e_fornecedor_compartilham_cadastro(self):
        cli = Cliente.objects.create(nome="Alfa Ltda", documento="11.222.333/0001-81", cidade="Campinas")
        self.assertEqual(cli.documento, CNPJ, "documento é guardado só com dígitos")
        forn = cli.adicionar_papel("fornecedor", categoria="serviços")
        self.assertEqual(ParceiroNegocio.objects.count(), 1)
        self.assertEqual(forn.parceironegocio_id, cli.parceironegocio_id)
        self.assertEqual(forn.cidade, "Campinas")
        self.assertEqual(cli.papeis(), ["Cliente", "Fornecedor"])

        # Mudar o endereço por um papel muda para o outro.
        forn.cidade = "Jundiaí"
        forn.save()
        cli.refresh_from_db()
        self.assertEqual(cli.cidade, "Jundiaí")

        # Os IDs de cada papel são independentes do cadastro-mãe e servem às chaves estrangeiras.
        cp = ContaPagar.objects.create(fornecedor=forn, descricao="x", valor=10, vencimento="2026-12-01")
        self.assertEqual(cp.fornecedor_id, forn.pk)

    def test_cnpj_duplicado_e_barrado(self):
        Cliente.objects.create(nome="Alfa", documento=CNPJ)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Fornecedor.objects.create(nome="Outra", documento=CNPJ)
        # Sem documento não há conflito.
        Fornecedor.objects.create(nome="Sem CNPJ 1")
        Fornecedor.objects.create(nome="Sem CNPJ 2")

    def test_formulario_avisa_duplicidade(self):
        Cliente.objects.create(nome="Alfa", documento=CNPJ)
        r = self.client.post(reverse("core:fornecedor_create"), {
            "tipo_pessoa": "PJ", "nome": "Alfa de novo", "documento": "11.222.333/0001-81",
            "prazo_pagamento_dias": 30, "ativo": "on",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Tornar fornecedor")
        self.assertEqual(Fornecedor.objects.count(), 0)

    def test_editar_papel_nao_acusa_duplicidade_consigo(self):
        forn = Fornecedor.objects.create(nome="Beta", documento=CNPJ)
        r = self.client.post(reverse("core:fornecedor_update", args=[forn.pk]), {
            "tipo_pessoa": "PJ", "nome": "Beta S.A.", "documento": CNPJ, "prazo_pagamento_dias": 45, "ativo": "on",
        })
        self.assertEqual(r.status_code, 302, r.content[:2000])
        forn.refresh_from_db()
        self.assertEqual((forn.nome, forn.prazo_pagamento_dias), ("Beta S.A.", 45))

    def test_tela_tornar_cliente(self):
        forn = Fornecedor.objects.create(nome="Gama", documento=CNPJ)
        url = reverse("core:tornar_cliente", args=[forn.pk])
        self.assertContains(self.client.get(url), "passará a ser também cliente")
        r = self.client.post(url)
        cli = Cliente.objects.get(parceironegocio_id=forn.parceironegocio_id)
        self.assertRedirects(r, reverse("core:cliente_update", args=[cli.pk]))
        # Repetir não cria outro.
        self.client.post(url)
        self.assertEqual(Cliente.objects.count(), 1)

    def test_excluir_um_papel_mantem_o_outro(self):
        cli = Cliente.objects.create(nome="Delta", documento=CNPJ)
        forn = cli.adicionar_papel("fornecedor")
        forn.delete()
        self.assertTrue(Cliente.objects.filter(pk=cli.pk).exists())
        self.assertEqual(ParceiroNegocio.objects.count(), 1)
        Cliente.objects.get(pk=cli.pk).delete()
        self.assertEqual(ParceiroNegocio.objects.count(), 0)

    def test_importar_nfe_de_quem_ja_e_cliente(self):
        cli = Cliente.objects.create(nome="Emitente cliente", documento=CNPJ)
        nota = importar_nfe(SimpleUploadedFile("n.xml", XML_NFE.encode(), content_type="text/xml"))
        self.assertEqual(nota.fornecedor.parceironegocio_id, cli.parceironegocio_id)
        self.assertEqual(ParceiroNegocio.objects.count(), 1)

    def test_lista_de_parceiros_mostra_papeis(self):
        cli = Cliente.objects.create(nome="Épsilon", documento=CNPJ)
        cli.adicionar_papel("fornecedor")
        r = self.client.get(reverse("core:parceironegocio_list"))
        self.assertContains(r, "Cliente, Fornecedor")
        self.assertEqual(self.client.get(reverse("core:parceironegocio_detail",
                                                 args=[cli.parceironegocio_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:cliente_detail", args=[cli.pk])).status_code, 200)
