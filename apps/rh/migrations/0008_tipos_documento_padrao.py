from django.db import migrations


def criar(apps, schema_editor):
    from apps.rh.documentos_pessoais import PADRAO
    Tipo = apps.get_model("rh", "TipoDocumentoPessoal")
    for nome, alvo, clt, imin, imax, val, ordem in PADRAO:
        Tipo.objects.get_or_create(nome=nome, alvo=alvo, defaults=dict(
            somente_clt=clt, idade_minima=imin, idade_maxima=imax, exige_validade=val, ordem=ordem))


class Migration(migrations.Migration):
    dependencies = [("rh", "0007_documentos_pessoais")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
