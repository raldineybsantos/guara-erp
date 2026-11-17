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
    from apps.tarefas.models import Atividade
    minhas = Atividade.objects.minhas(u).exclude(status__in=["concluida", "cancelada"]).distinct()
    ctx["atividades_abertas"] = minhas.count()
    ctx["atividades_atrasadas"] = minhas.filter(prazo__lt=hoje).count()
    if u.has_perm("juridico.view_contrato"):
        from apps.juridico.models import Contrato
        ctx["contratos_vencer"] = [c for c in Contrato.objects.filter(status="vigente", fim_vigencia__gte=hoje)
                                   .select_related("fornecedor", "cliente").order_by("fim_vigencia")[:50]
                                   if c.dias_para_vencer <= c.alerta_dias][:6]
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

    def get_context_data(self, **kwargs):
        from apps.identidade.models import ConfiguracaoLDAP, ProvedorOIDC, ProvedorSAML

        ctx = super().get_context_data(**kwargs)
        ctx["provedores_sso"] = ProvedorOIDC.objects.filter(ativo=True)
        ctx["provedores_saml"] = ProvedorSAML.objects.filter(ativo=True)
        ctx["tem_ldap"] = ConfiguracaoLDAP.objects.filter(ativo=True).exists()
        return ctx

    def form_valid(self, form):
        resposta = super().form_valid(form)
        from apps.seguranca import mfa

        mfa.iniciar_sessao(self.request, form.get_user())
        return resposta


@login_required
def avisos(request):
    """Lista de avisos do usuário. Abrir um aviso marca como lido e leva ao registro."""
    from django.shortcuts import get_object_or_404

    from .models import Notificacao

    qs = Notificacao.objects.filter(usuario=request.user)
    if request.method == "POST":
        if request.POST.get("abrir"):
            n = get_object_or_404(qs, pk=request.POST["abrir"])
            n.lida = True
            n.save(update_fields=["lida"])
            from django.utils.http import url_has_allowed_host_and_scheme

            destino = n.url if url_has_allowed_host_and_scheme(n.url, None) and n.url.startswith("/") else "avisos"
            return redirect(destino)
        qs.filter(lida=False).update(lida=True)
        return redirect("avisos")
    return render(request, "avisos.html", {"titulo": "Avisos", "icone": "bell", "avisos": qs[:100]})


@login_required
def trocar_empresa(request):
    """Escolhe a empresa em que o usuário trabalha (padrão dos novos lançamentos e das notas emitidas)."""
    from django.utils.http import url_has_allowed_host_and_scheme

    from .models import Empresa

    if request.method == "POST":
        emp = Empresa.acessiveis(request.user).filter(pk=request.POST.get("empresa") or 0).first()
        if emp is None:
            messages.error(request, "Você não tem acesso a essa empresa.")
        else:
            request.session["empresa_id"] = emp.pk
            messages.success(request, f"Agora você está trabalhando em {emp}.")
    destino = request.POST.get("next") or "/"
    return redirect(destino if url_has_allowed_host_and_scheme(destino, None) and destino.startswith("/") else "/")
