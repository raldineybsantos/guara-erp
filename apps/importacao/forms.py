import os

from django import forms
from django.core.exceptions import ValidationError

from apps.core.validators import validar_arquivo_seguro
from apps.seguranca.antivirus import validador as validar_antivirus

from .conectores import TIPOS
from .leitores import ACEITOS, formato_de


def _validar_planilha(arquivo):
    validar_arquivo_seguro(arquivo)
    formato_de(arquivo.name)
    validar_antivirus(arquivo)


class ArquivoForm(forms.Form):
    arquivo = forms.FileField(label="Arquivo", validators=[_validar_planilha],
                              widget=forms.ClearableFileInput(attrs={"accept": ACEITOS}),
                              help_text="Excel, CSV, JSON, SQLite, dump .sql (MariaDB/MySQL, PostgreSQL, SQL Server) ou Access.")


class BancoForm(forms.Form):
    tipo = forms.ChoiceField(label="Tipo de banco", choices=[(k, r) for k, r, _ in TIPOS])
    host = forms.CharField(label="Servidor", max_length=200)
    porta = forms.IntegerField(label="Porta", required=False, min_value=1, max_value=65535)
    banco = forms.CharField(label="Nome do banco", max_length=120)
    usuario = forms.CharField(label="Usuário", max_length=120)
    senha = forms.CharField(label="Senha", widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
                            required=False)
