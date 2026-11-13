"""Parceiro de negócios único (como o Business Partner do SAP).

Cliente e Fornecedor deixam de guardar nome, CPF/CNPJ, endereço e contato: esses dados passam
para ``ParceiroNegocio``, e os dois viram papéis (herança multi-tabela). Um CPF/CNPJ que era
cliente e fornecedor ao mesmo tempo vira um parceiro só, com os dois papéis.

Os IDs de Cliente e Fornecedor não mudam (a coluna ``id`` continua sendo a chave, agora chamada
``codigo`` no Django), então notas, contas, contratos e cobranças continuam apontando para o mesmo
registro.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.operations.base import Operation

import apps.core.validators

COMUNS = [
    "tipo_pessoa", "nome", "nome_fantasia", "documento", "inscricao_estadual", "email", "telefone",
    "contato", "ativo", "observacoes", "cep", "logradouro", "numero", "complemento", "bairro",
    "cidade", "uf",
]
CONTROLE = ["criado_em", "atualizado_em", "criado_por"]


class AlterarBases(Operation):
    """Troca as classes-base de um modelo no estado das migrações (o Django não tem operação própria)."""

    reduces_to_sql = False
    reversible = True

    def __init__(self, name, bases, bases_antigas):
        self.name = name
        self.bases = bases
        self.bases_antigas = bases_antigas

    def deconstruct(self):
        return self.__class__.__name__, [], {"name": self.name, "bases": self.bases,
                                              "bases_antigas": self.bases_antigas}

    def _trocar(self, app_label, state, bases):
        chave = (app_label, self.name.lower())
        state.models[chave].bases = bases
        state.reload_model(app_label, self.name.lower(), delay=False)

    def state_forwards(self, app_label, state):
        self._trocar(app_label, state, self.bases)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def state_backwards(self, app_label, state):  # pragma: no cover - usado só ao desfazer
        self._trocar(app_label, state, self.bases_antigas)

    def describe(self):
        return f"Altera as bases de {self.name}"


def _digitos(valor):
    return "".join(c for c in (valor or "") if c.isdigit())


def unificar(apps, schema_editor):
    Parceiro = apps.get_model("core", "ParceiroNegocio")
    por_documento = {}

    for nome_modelo in ("Fornecedor", "Cliente"):
        Modelo = apps.get_model("core", nome_modelo)
        for papel in Modelo.objects.order_by("id"):
            doc = _digitos(papel.documento)
            dados = {c: getattr(papel, c) for c in COMUNS}
            dados["documento"] = doc
            pn = por_documento.get(doc) if doc else None
            if pn is not None and Modelo.objects.filter(parceironegocio_id=pn.pk).exists():
                # Mesmo papel duas vezes com o mesmo CPF/CNPJ: mantém os dois, sem o documento no segundo.
                dados["documento"] = ""
                dados["observacoes"] = (
                    f"{dados['observacoes']}\nCPF/CNPJ {doc} duplicado em outro {nome_modelo.lower()}; "
                    "revise e unifique os cadastros."
                ).strip()
                pn = None
            if pn is None:
                pn = Parceiro.objects.create(**dados, criado_por_id=papel.criado_por_id)
                Parceiro.objects.filter(pk=pn.pk).update(criado_em=papel.criado_em)
                if dados["documento"]:
                    por_documento[doc] = pn
            else:
                # Já existe como o outro papel: completa só o que estiver em branco.
                mudou = False
                for c in COMUNS:
                    if c != "documento" and not getattr(pn, c) and dados[c]:
                        setattr(pn, c, dados[c])
                        mudou = True
                if mudou:
                    pn.save()
            papel.parceironegocio_id = pn.pk
            papel.save(update_fields=["parceironegocio"])


def desfazer(apps, schema_editor):  # pragma: no cover
    raise RuntimeError("A unificação de parceiros não pode ser desfeita automaticamente; restaure o backup.")


def _remocoes(modelo):
    return [migrations.RemoveField(model_name=modelo, name=c) for c in COMUNS + CONTROLE]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_log_ip"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ParceiroNegocio",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("criado_em", models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                ("atualizado_em", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
                ("cep", models.CharField(blank=True, max_length=9, verbose_name="CEP")),
                ("logradouro", models.CharField(blank=True, max_length=200)),
                ("numero", models.CharField(blank=True, max_length=20, verbose_name="número")),
                ("complemento", models.CharField(blank=True, max_length=100)),
                ("bairro", models.CharField(blank=True, max_length=100)),
                ("cidade", models.CharField(blank=True, max_length=100)),
                ("uf", models.CharField(blank=True, choices=[(uf, uf) for uf in (
                    "AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split()
                )], max_length=2, verbose_name="UF")),
                ("tipo_pessoa", models.CharField(choices=[("PJ", "Pessoa Jurídica"), ("PF", "Pessoa Física")],
                                                 default="PJ", max_length=2, verbose_name="tipo de pessoa")),
                ("nome", models.CharField(max_length=200, verbose_name="nome / razão social")),
                ("nome_fantasia", models.CharField(blank=True, max_length=200, verbose_name="nome fantasia")),
                ("documento", models.CharField(blank=True, max_length=18,
                                               validators=[apps.core.validators.validar_cpf_cnpj],
                                               verbose_name="CPF/CNPJ")),
                ("inscricao_estadual", models.CharField(blank=True, max_length=30, verbose_name="inscrição estadual")),
                ("email", models.EmailField(blank=True, max_length=254, verbose_name="e-mail")),
                ("telefone", models.CharField(blank=True, max_length=30)),
                ("contato", models.CharField(blank=True, max_length=120, verbose_name="pessoa de contato")),
                ("ativo", models.BooleanField(default=True)),
                ("observacoes", models.TextField(blank=True, verbose_name="observações")),
                ("criado_por", models.ForeignKey(blank=True, editable=False, null=True,
                                                 on_delete=django.db.models.deletion.SET_NULL, related_name="+",
                                                 to=settings.AUTH_USER_MODEL, verbose_name="criado por")),
            ],
            options={
                "verbose_name": "parceiro de negócios",
                "verbose_name_plural": "parceiros de negócios",
                "ordering": ["nome"],
            },
        ),
        migrations.AddField(
            model_name="fornecedor",
            name="parceironegocio",
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name="fornecedor", to="core.parceironegocio",
                                       verbose_name="parceiro de negócios"),
        ),
        migrations.AddField(
            model_name="cliente",
            name="parceironegocio",
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE,
                                       related_name="cliente", to="core.parceironegocio",
                                       verbose_name="parceiro de negócios"),
        ),
        migrations.RunPython(unificar, desfazer),
        # A chave de cada papel continua na coluna "id"; no Django passa a se chamar "codigo",
        # porque "id" agora é herdado do parceiro.
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.RenameField("fornecedor", "id", "codigo"),
            migrations.AlterField("fornecedor", "codigo", models.BigAutoField(
                db_column="id", primary_key=True, serialize=False, verbose_name="código")),
            migrations.RenameField("cliente", "id", "codigo"),
            migrations.AlterField("cliente", "codigo", models.BigAutoField(
                db_column="id", primary_key=True, serialize=False, verbose_name="código")),
        ]),
        *_remocoes("fornecedor"),
        *_remocoes("cliente"),
        migrations.AlterField(
            model_name="fornecedor",
            name="parceironegocio",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True,
                                       related_name="fornecedor", to="core.parceironegocio",
                                       verbose_name="parceiro de negócios"),
        ),
        migrations.AlterField(
            model_name="cliente",
            name="parceironegocio",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, parent_link=True,
                                       related_name="cliente", to="core.parceironegocio",
                                       verbose_name="parceiro de negócios"),
        ),
        AlterarBases("Fornecedor", ("core.parceironegocio",), (models.Model,)),
        AlterarBases("Cliente", ("core.parceironegocio",), (models.Model,)),
        migrations.AddConstraint(
            model_name="parceironegocio",
            constraint=models.UniqueConstraint(
                condition=models.Q(("documento", ""), _negated=True), fields=("documento",),
                name="parceiro_documento_unico",
                violation_error_message=(
                    "Este CPF/CNPJ já está cadastrado como parceiro de negócios. Abra o cadastro existente "
                    "e use “Tornar cliente” ou “Tornar fornecedor” para adicionar o papel."
                ),
            ),
        ),
    ]
