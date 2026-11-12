from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.static import serve

from apps.core import views as core_views

admin.site.site_header = "ERP Empresa - Administração"
admin.site.site_title = "ERP Empresa"
admin.site.index_title = "Administração do sistema"

urlpatterns = [
    path("", core_views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("senha/", auth_views.PasswordChangeView.as_view(template_name="form_simples.html",
                                                          extra_context={"titulo": "Alterar senha", "icone": "key"},
                                                          success_url="/"), name="password_change"),
    path("banco-de-dados/", core_views.banco_dados, name="banco_dados"),
    path("banco-de-dados/backup/", core_views.banco_backup, name="banco_backup"),
    path("banco-de-dados/manutencao/", core_views.banco_manutencao, name="banco_manutencao"),
    path("cadastros/", include("apps.core.urls")),
    path("financeiro/", include("apps.financeiro.urls")),
    path("cobranca/", include("apps.cobranca.urls")),
    path("fiscal/", include("apps.fiscal.urls")),
    path("workflow/", include("apps.workflow.urls")),
    path("ged/", include("apps.ged.urls")),
    path("admin/", admin.site.urls),
]

# Arquivos enviados (XML de notas). Documentos do GED são servidos pela view com controle de acesso.
urlpatterns += [path("media/fiscal/<path:path>", login_required(serve), {"document_root": settings.MEDIA_ROOT / "fiscal"})]
