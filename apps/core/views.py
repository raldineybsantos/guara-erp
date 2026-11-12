import datetime
import os
import shutil
import subprocess
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.financeiro.models import ContaBancaria, ContaPagar, ContaReceber
from apps.financeiro.views import fluxo_caixa_dados
from apps.workflow.models import Instancia

from .models import Colaborador, LogAuditoria


@login_required
def dashboard(request):
    hoje = timezone.localdate()
    u = request.user
    ctx = {"titulo": "Painel", "icone": "speedometer2", "hoje": hoje}

    if u.has_perm("financeiro.view_contapagar"):
        pagar = ContaPagar.objects.em_aberto()
        ctx["pagar_vencidos"] = pagar.filter(vencimento__lt=hoje)
        ctx["pagar_hoje"] = pagar.filter(vencimento=hoje)
        ctx["pagar_proximos"] = pagar.filter(vencimento__gte=hoje).select_related("fornecedor")[:8]
        ctx["total_pagar"] = sum((t.saldo for t in pagar), Decimal(0))
        ctx["total_pagar_vencido"] = sum((t.saldo for t in ctx["pagar_vencidos"]), Decimal(0))
    if u.has_perm("financeiro.view_contareceber"):
        receber = ContaReceber.objects.em_aberto()
        ctx["receber_vencidos"] = receber.filter(vencimento__lt=hoje)
        ctx["receber_proximos"] = receber.filter(vencimento__gte=hoje).select_related("cliente")[:8]
        ctx["total_receber"] = sum((t.saldo for t in receber), Decimal(0))
        ctx["total_receber_vencido"] = sum((t.saldo for t in ctx["receber_vencidos"]), Decimal(0))
    if u.has_perm("financeiro.view_contabancaria"):
        contas = list(ContaBancaria.objects.filter(ativa=True))
        ctx["contas"] = contas
        ctx["saldo_total"] = sum((c.saldo_atual for c in contas), Decimal(0))
    if u.has_perm("financeiro.view_movimentacao"):
        ctx["fluxo"] = fluxo_caixa_dados(6, 3)
    if u.has_perm("core.view_colaborador"):
        ctx["colaboradores_ativos"] = Colaborador.objects.filter(status="ativo").count()
    ctx["tarefas"] = Instancia.objects.pendentes_para(u).select_related("processo", "etapa_atual")[:8]
    return render(request, "dashboard.html", ctx)


def _somente_superusuario(user):
    return user.is_active and user.is_superuser


@login_required
@user_passes_test(_somente_superusuario)
def banco_dados(request):
    """Administração básica do PostgreSQL pela interface web (complementa o pgAdmin)."""
    with connection.cursor() as cur:
        cur.execute("SELECT version()")
        versao = cur.fetchone()[0]
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())), current_database()")
        tamanho, nome_banco = cur.fetchone()
        cur.execute("""
            SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)),
                   last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
            FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC
        """)
        tabelas = cur.fetchall()
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        conexoes = cur.fetchone()[0]
        cur.execute("SELECT current_user, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
        usuario_db, eh_super, cria_db, cria_role = cur.fetchone()
    return render(request, "banco_dados.html", {
        "titulo": "Banco de dados", "icone": "database", "versao": versao, "tamanho": tamanho,
        "nome_banco": nome_banco, "tabelas": tabelas, "conexoes": conexoes,
        "pg_dump": bool(shutil.which("pg_dump")), "host": settings.DATABASES["default"]["HOST"],
        "logs": LogAuditoria.objects.select_related("usuario")[:15],
        "usuario_db": usuario_db, "eh_super": eh_super, "cria_db": cria_db, "cria_role": cria_role,
        "debug": settings.DEBUG, "https": settings.SESSION_COOKIE_SECURE,
        "falhas_login": LogAuditoria.objects.filter(
            modelo="auth.login", descricao__startswith="FALHA",
            data__gte=timezone.now() - datetime.timedelta(days=1)).count(),
    })


@login_required
@user_passes_test(_somente_superusuario)
def banco_manutencao(request):
    if request.method == "POST":
        with connection.cursor() as cur:
            cur.execute("VACUUM ANALYZE")
        messages.success(request, "Manutenção (VACUUM/ANALYZE) executada.")
    return redirect("banco_dados")


@login_required
@user_passes_test(_somente_superusuario)
def banco_backup(request):
    """Gera um dump SQL do banco e envia para download (requer pg_dump no servidor)."""
    if not shutil.which("pg_dump"):
        messages.error(request, "pg_dump não encontrado no servidor. Instale o cliente do PostgreSQL.")
        return redirect("banco_dados")
    db = settings.DATABASES["default"]
    env = dict(os.environ, PGPASSWORD=db["PASSWORD"])
    cmd = ["pg_dump", "-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"], "--no-owner", db["NAME"]]
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=600)
    if proc.returncode != 0:
        messages.error(request, f"Falha no backup: {proc.stderr.decode(errors='ignore')[:300]}")
        return redirect("banco_dados")
    LogAuditoria.objects.create(usuario=request.user, acao="acao", modelo="banco", descricao="Backup do banco gerado")
    resp = HttpResponse(proc.stdout, content_type="application/sql")
    resp["Content-Disposition"] = f'attachment; filename="erp_{timezone.now():%Y%m%d_%H%M}.sql"'
    return resp


# --------------------------------------------------------------------------- login com bloqueio
from django.contrib.auth import views as auth_views  # noqa: E402

from .signals import ip_da_requisicao  # noqa: E402


class LoginView(auth_views.LoginView):
    """Bloqueia temporariamente após várias senhas erradas (por usuário e por IP)."""

    template_name = "login.html"
    redirect_authenticated_user = True

    def bloqueado(self):
        limite = settings.LOGIN_MAX_TENTATIVAS
        desde = timezone.now() - datetime.timedelta(minutes=settings.LOGIN_BLOQUEIO_MINUTOS)
        falhas = LogAuditoria.objects.filter(modelo="auth.login", descricao__startswith="FALHA", data__gte=desde)
        username = (self.request.POST.get("username") or "").strip()[:50]
        ip = ip_da_requisicao(self.request)
        por_usuario = falhas.filter(objeto_id__iexact=username).count() if username else 0
        por_ip = falhas.filter(ip=ip).count() if ip else 0
        return por_usuario >= limite or por_ip >= limite * 3

    def post(self, request, *args, **kwargs):
        if self.bloqueado():
            form = self.get_form()
            form.errors.clear()
            return self.render_to_response(self.get_context_data(form=form, bloqueado=True), status=429)
        return super().post(request, *args, **kwargs)
