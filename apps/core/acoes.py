"""Telas de ação padronizadas: confirmar e executar um método do registro, com ou sem formulário."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render


def confirmar(request, obj, titulo, mensagem, func, ok, destino=None):
    """GET mostra a confirmação; POST executa ``func()`` e volta ao registro (ou a ``destino(resultado)``)."""
    if request.method != "POST":
        return render(request, "confirmar.html", {"titulo": titulo, "mensagem": mensagem,
                                                  "voltar": obj.get_absolute_url()})
    try:
        resultado = func()
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        return redirect(obj.get_absolute_url())
    messages.success(request, ok(resultado) if callable(ok) else ok)
    return redirect(destino(resultado) if destino and resultado is not None else obj.get_absolute_url())


def formulario(request, obj, form_class, titulo, func, ok, icone="pencil", initial=None, destino=None):
    """Formulário simples; no POST válido executa ``func(cleaned_data)``."""
    form = form_class(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            resultado = func(form.cleaned_data)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, ok(resultado) if callable(ok) else ok)
            return redirect(destino(resultado) if destino and resultado is not None else obj.get_absolute_url())
    return render(request, "form_simples.html", {"form": form, "titulo": titulo, "icone": icone})
