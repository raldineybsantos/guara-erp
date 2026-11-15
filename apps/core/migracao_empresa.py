"""Apoio às migrações que adicionam o campo `empresa` a cadastros que já tinham dados: os registros existentes
passam a pertencer à empresa padrão (a matriz), que era a única em uso até então."""


def empresa_padrao(apps):
    Empresa = apps.get_model("core", "Empresa")
    return Empresa.objects.filter(ativa=True).order_by("-matriz", "pk").first()


def preencher(*modelos):
    def executar(apps, schema_editor):
        padrao = empresa_padrao(apps)
        if padrao is None:
            return
        for rotulo in modelos:
            apps.get_model(rotulo).objects.filter(empresa__isnull=True).update(empresa=padrao)
    return executar


def preencher_lancamentos(apps, schema_editor):
    """Lançamento herda a empresa do documento que o originou; sem origem identificável, a empresa padrão."""
    padrao = empresa_padrao(apps)
    if padrao is None:
        return
    Lancamento = apps.get_model("contabilidade", "Lancamento")
    ContentType = apps.get_model("contenttypes", "ContentType")
    pendentes = Lancamento.objects.filter(empresa__isnull=True)
    for ct in ContentType.objects.filter(pk__in=pendentes.exclude(content_type=None).values("content_type")):
        try:
            modelo = apps.get_model(ct.app_label, ct.model)
            modelo._meta.get_field("empresa")
        except (LookupError, Exception):
            continue
        for obj in modelo.objects.filter(pk__in=pendentes.filter(content_type=ct).values("object_id"),
                                         empresa__isnull=False).only("pk", "empresa"):
            pendentes.filter(content_type=ct, object_id=obj.pk).update(empresa_id=obj.empresa_id)
    Lancamento.objects.filter(empresa__isnull=True).update(empresa=padrao)
