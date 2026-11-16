# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django import forms

from apps.core.crud import BootstrapMixin

from .models import ConfiguracaoLDAP, ProvedorOIDC


class LDAPForm(BootstrapMixin, forms.ModelForm):
    senha_servico = forms.CharField(label="senha da conta de serviço", required=False,
                                    widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                                    help_text="Fica cifrada. Deixe em branco para manter a atual.")

    class Meta:
        model = ConfiguracaoLDAP
        exclude = ["bind_senha"]
        widgets = {"ca_pem": forms.Textarea(attrs={"rows": 4}), "mapa_grupos": forms.Textarea(attrs={"rows": 4})}

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("senha_servico"):
            obj.definir_senha(self.cleaned_data["senha_servico"])
        if commit:
            obj.save()
        return obj


class OIDCForm(BootstrapMixin, forms.ModelForm):
    segredo = forms.CharField(label="segredo do aplicativo (client secret)", required=False,
                              widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                              help_text="Fica cifrado. Deixe em branco para manter o atual.")

    class Meta:
        model = ProvedorOIDC
        exclude = ["client_secret", "slug"]

    def clean(self):
        dados = super().clean()
        if not self.instance.client_secret and not dados.get("segredo"):
            self.add_error("segredo", "Informe o segredo do aplicativo.")
        return dados

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("segredo"):
            obj.definir_segredo(self.cleaned_data["segredo"])
        if commit:
            obj.save()
        return obj
