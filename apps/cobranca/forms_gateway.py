# Guará ERP — gestão empresarial livre
# Copyright (C) 2026 Raldiney Santos e colaboradores
# SPDX-License-Identifier: GPL-3.0-or-later
from django import forms

from apps.core.crud import BootstrapMixin

from .models import ConfiguracaoGateway


class GatewayForm(BootstrapMixin, forms.ModelForm):
    chave = forms.CharField(label="chave da API (Efí: Client_Secret)", required=False, widget=forms.PasswordInput(
        render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Painel do gateway → Integrações. Fica cifrada. Em branco mantém a atual. Sandbox e produção têm chaves diferentes.")

    class Meta:
        model = ConfiguracaoGateway
        exclude = ["chave_api", "token_webhook"]

    def clean(self):
        d = super().clean()
        if not self.instance.chave_api and not d.get("chave"):
            self.add_error("chave", "Informe a chave da API.")
        return d

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("chave"):
            obj.definir_chave(self.cleaned_data["chave"])
        if not obj.token_webhook:
            obj.novo_token_webhook()
        if commit:
            obj.save()
        return obj
