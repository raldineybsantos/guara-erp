from django import forms

from apps.core.crud import Crud, FormBase
from apps.core.validators import validar_arquivo_seguro
from apps.seguranca.antivirus import validador as validar_antivirus

from .models import Documento, Pasta


class DocumentoForm(FormBase):
    precisa_request = True
    arquivo = forms.FileField(label="Arquivo", required=False, validators=[validar_arquivo_seguro, validar_antivirus],
                              help_text="Na edição, enviar um arquivo cria uma nova versão. O arquivo passa pelo antivírus antes de ser guardado.")
    comentario_versao = forms.CharField(label="Comentário da versão", required=False, max_length=300)

    class Meta:
        model = Documento
        fields = ["pasta", "titulo", "tipo", "descricao", "tags", "validade", "confidencial"]

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        if request is not None:
            self.fields["pasta"].queryset = Pasta.visiveis_para(request.user)
        if not self.instance.pk:
            self.fields["arquivo"].required = True

    def save(self, commit=True):
        doc = super().save(commit=commit)
        arquivo = self.cleaned_data.get("arquivo")
        if arquivo and commit:
            doc.adicionar_versao(arquivo, self.cleaned_data.get("comentario_versao", ""),
                                 getattr(self.request, "user", None))
        return doc


def _qs_visivel(modelo):
    @classmethod
    def get_queryset(cls, request):
        return modelo.visiveis_para(request.user).select_related(*(
            ["pasta"] if modelo is Documento else ["superior"]))
    return get_queryset


class PastaCrud(Crud):
    model = Pasta
    fields = ["nome", "superior", "departamento", "grupos_acesso"]
    list_display = ("caminho", "departamento", "grupos_acesso")
    search_fields = ("nome",)
    related = (("documentos", "Documentos", ["titulo", "tipo", "num_versao", "validade"]),
               ("subpastas", "Subpastas", ["nome"]))
    icon = "folder2-open"
    menu = "GED"
    menu_order = 2
    get_queryset = _qs_visivel(Pasta)


class DocumentoCrud(Crud):
    model = Documento
    form_class = DocumentoForm
    list_display = ("titulo", "pasta", "tipo", "num_versao", "validade", "confidencial", "atualizado_em")
    search_fields = ("titulo", "descricao", "tags", "versoes__nome_original")
    filter_fields = ("pasta", "tipo", "confidencial")
    detail_template = "ged/documento_detail.html"
    icon = "file-earmark-richtext"
    menu = "GED"
    menu_order = 1
    get_queryset = _qs_visivel(Documento)
