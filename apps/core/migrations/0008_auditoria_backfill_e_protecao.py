from django.db import migrations

from apps.core import auditoria

BLOQUEIO_LOG = """
CREATE OR REPLACE FUNCTION core_auditoria_protegida() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'A trilha de auditoria não pode ser apagada.' USING ERRCODE = '42501';
  END IF;
  -- UPDATE: só o que o próprio banco faz quando um usuário ou empresa é excluído (zera a chave estrangeira)
  IF (to_jsonb(NEW) - 'usuario_id' - 'empresa_id') = (to_jsonb(OLD) - 'usuario_id' - 'empresa_id')
     AND (NEW.usuario_id IS NULL OR NEW.usuario_id = OLD.usuario_id)
     AND (NEW.empresa_id IS NULL OR NEW.empresa_id = OLD.empresa_id) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'A trilha de auditoria não pode ser alterada.' USING ERRCODE = '42501';
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS auditoria_protegida ON core_logauditoria;
CREATE TRIGGER auditoria_protegida BEFORE UPDATE OR DELETE ON core_logauditoria
  FOR EACH ROW EXECUTE FUNCTION core_auditoria_protegida();

CREATE OR REPLACE FUNCTION core_selo_protegido() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'Os selos da auditoria não podem ser alterados nem apagados.' USING ERRCODE = '42501';
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS selo_protegido ON core_seloauditoria;
CREATE TRIGGER selo_protegido BEFORE UPDATE OR DELETE ON core_seloauditoria
  FOR EACH ROW EXECUTE FUNCTION core_selo_protegido();
"""

DESFAZER = """
DROP TRIGGER IF EXISTS auditoria_protegida ON core_logauditoria;
DROP TRIGGER IF EXISTS selo_protegido ON core_seloauditoria;
DROP FUNCTION IF EXISTS core_auditoria_protegida();
DROP FUNCTION IF EXISTS core_selo_protegido();
"""


def preencher(apps, schema_editor):
    """Registros anteriores ganham o nome do usuário e o hash (antes do gatilho passar a bloquear alterações)."""
    Log = apps.get_model("core", "LogAuditoria")
    for log in Log.objects.filter(hash="").select_related("usuario").iterator():
        nome = log.usuario.get_username() if log.usuario_id else "sistema"
        dados = {c: getattr(log, c) for c in auditoria.CAMPOS_HASH}
        dados["usuario_nome"] = nome
        Log.objects.filter(pk=log.pk).update(usuario_nome=nome, hash=auditoria.hash_registro(dados))


class Migration(migrations.Migration):
    dependencies = [("core", "0007_seloauditoria_alter_logauditoria_options_and_more")]
    atomic = False   # preencher e só então proteger, sem eventos de gatilho pendentes na mesma transação

    operations = [
        migrations.RunPython(preencher, migrations.RunPython.noop),
        migrations.RunSQL(BLOQUEIO_LOG, DESFAZER),
    ]
