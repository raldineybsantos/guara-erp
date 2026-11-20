import csv
import json
import os
import tempfile
import time

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.fiscal.cofre import cifrar, decifrar

from . import conectores, leitores, servico, sugestao
from .destinos import destino as obter_destino, destinos
from .forms import ArquivoForm, BancoForm
from .models import Importacao

SESSAO = "importacao_conexoes"
VALIDADE_SENHA = 15 * 60


def _destinos_do_usuario(user):
    return [d for d in destinos().values() if d.pode(user)]


def _minha(request, pk):
    """Importação do próprio usuário (quem tem 'ver todas' só consulta as dos outros)."""
    return get_object_or_404(Importacao, pk=pk, usuario=request.user)


def _consultavel(request, pk):
    qs = Importacao.objects.all() if request.user.has_perm("importacao.ver_todas_importacoes") \
        else Importacao.objects.filter(usuario=request.user)
    return get_object_or_404(qs, pk=pk)


def _guardar_conexao(request, imp, senha):
    s = request.session.get(SESSAO, {})
    s[str(imp.pk)] = {"senha": cifrar(senha.encode()).decode(), "ate": time.time() + VALIDADE_SENHA}
    request.session[SESSAO] = s


def _senha_da_conexao(request, imp):
    d = request.session.get(SESSAO, {}).get(str(imp.pk))
    if not d or d["ate"] < time.time():
        return None
    return decifrar(d["senha"].encode()).decode()


def _esquecer_conexao(request, imp):
    s = request.session.get(SESSAO, {})
    if s.pop(str(imp.pk), None) is not None:
        request.session[SESSAO] = s


def _guardar_tabela(imp, tabela):
    with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as t:
        caminho = t.name
    try:
        leitores.salvar_tabela(tabela, caminho)
        with open(caminho, "rb") as f:
            imp.dados.save("dados.json.gz", File(f), save=False)
    finally:
        os.unlink(caminho)
    imp.colunas = tabela.colunas
    imp.total_linhas = tabela.total
    imp.lidas = len(tabela.linhas)
    imp.tabela_origem = tabela.nome[:200]
    imp.status = "lido"
    imp.destino, imp.mapeamento, imp.resultado = "", {}, {}
    imp.save()


# --------------------------------------------------------------------------- lista e criação
@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
def lista(request):
    qs = Importacao.objects.select_related("usuario")
    if not request.user.has_perm("importacao.ver_todas_importacoes"):
        qs = qs.filter(usuario=request.user)
    return render(request, "importacao/lista.html", {
        "importacoes": qs[:100], "titulo": "Importação de dados", "icone": "cloud-upload",
        "pode_banco": request.user.has_perm("importacao.conectar_banco")})


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
def nova(request):
    form = ArquivoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        arq = form.cleaned_data["arquivo"]
        imp = Importacao(usuario=request.user, origem="arquivo", descricao=os.path.basename(arq.name)[:200])
        imp.arquivo_origem.save(arq.name, arq, save=False)
        imp.save()
        return redirect("importacao:tabela", pk=imp.pk)
    return render(request, "importacao/nova.html", {"form": form, "titulo": "Importar de um arquivo", "icone": "file-earmark-arrow-up"})


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
@permission_required("importacao.conectar_banco", raise_exception=True)
def nova_banco(request):
    form = BancoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            con = conectores.abrir(d["tipo"], d["host"], d["porta"], d["banco"], d["usuario"], d["senha"])
            con.close()
        except ValidationError as e:
            form.add_error(None, e)
        else:
            rotulo = dict((k, r) for k, r, _ in conectores.TIPOS)[d["tipo"]]
            imp = Importacao.objects.create(
                usuario=request.user, origem="banco", descricao=f"{rotulo}: {d['banco']}@{d['host']}"[:200],
                opcoes={"conexao": {"tipo": d["tipo"], "host": d["host"], "porta": d["porta"], "banco": d["banco"],
                                    "usuario": d["usuario"]}})
            _guardar_conexao(request, imp, d["senha"])
            return redirect("importacao:tabela", pk=imp.pk)
    return render(request, "importacao/nova_banco.html", {"form": form, "titulo": "Importar de um banco de dados",
                                                          "icone": "database"})


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
def tabela(request, pk):
    """Escolha da planilha/tabela de origem (pula direto quando só existe uma)."""
    imp = _minha(request, pk)
    if imp.status not in ("lido",) or imp.destino:
        return redirect("importacao:mapear", pk=imp.pk)
    con = None
    try:
        if imp.origem == "banco":
            c = imp.opcoes["conexao"]
            senha = _senha_da_conexao(request, imp)
            if senha is None:
                messages.error(request, "A conexão expirou; informe os dados do banco novamente.")
                imp.delete()
                return redirect("importacao:nova_banco")
            con = conectores.abrir(c["tipo"], c["host"], c["porta"], c["banco"], c["usuario"], senha)
            nomes = conectores.listar_tabelas(con, c["tipo"], c["banco"])
        else:
            nomes = leitores.tabelas(imp.arquivo_origem.path, imp.descricao)
        escolhida = request.POST.get("tabela") if request.method == "POST" else (nomes[0] if len(nomes) == 1 else None)
        if escolhida:
            if escolhida not in nomes:
                raise ValidationError("Tabela não encontrada.")
            if imp.origem == "banco":
                t = conectores.ler_tabela(con, c["tipo"], c["banco"], escolhida)
            else:
                t = leitores.ler(imp.arquivo_origem.path, imp.descricao, escolhida)
            if not t.linhas:
                raise ValidationError("A tabela escolhida não tem linhas.")
            _guardar_tabela(imp, t)
            imp.arquivo_origem.delete(save=False)           # o original não precisa ficar guardado
            imp.arquivo_origem = None
            imp.save()
            _esquecer_conexao(request, imp)
            return redirect("importacao:mapear", pk=imp.pk)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
        if request.method != "POST":
            imp.apagar_arquivos()
            imp.delete()
            return redirect("importacao:nova_banco" if imp.origem == "banco" else "importacao:nova")
        nomes = nomes if "nomes" in locals() else []
    finally:
        if con is not None:
            con.close()
    return render(request, "importacao/tabela.html", {"imp": imp, "nomes": nomes, "titulo": "Escolha a tabela", "icone": "table"})


# --------------------------------------------------------------------------- mapeamento
def _mapeamento_do_post(dest, post, colunas):
    saida = {}
    for c in dest.campos:
        col, fixo = post.get(f"col__{c.nome}", ""), post.get(f"fixo__{c.nome}", "").strip()
        if col in colunas:
            saida[c.nome] = {"coluna": col}
        elif fixo:
            saida[c.nome] = {"fixo": fixo[:200]}
    return saida


def _opcoes_do_post(post, user, antigas):
    from apps.core.models import Empresa
    emp = post.get("empresa", "")
    emp_ok = Empresa.acessiveis(user).filter(pk=emp).first() if emp.isdigit() else None
    op = {k: v for k, v in antigas.items() if k == "conexao"}
    op.update({
        "duplicados": post.get("duplicados") if post.get("duplicados") in dict(servico.DUPLICADOS) else "pular",
        "papel": post.get("papel") if post.get("papel") in dict(servico.PAPEIS) else "nenhum",
        "empresa": emp_ok.pk if emp_ok else None,
        "criar_relacionados": bool(post.get("criar_relacionados")),
        "tudo_ou_nada": bool(post.get("tudo_ou_nada")),
    })
    return op


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
def mapear(request, pk):
    from apps.core.models import Empresa
    imp = _minha(request, pk)
    if imp.status == "concluida" or imp.status == "desfeita" or not imp.dados:
        return redirect("importacao:detalhe", pk=imp.pk)
    permitidos = _destinos_do_usuario(request.user)
    tab = leitores.carregar_tabela(imp.dados.path)
    dest = obter_destino(imp.destino) if imp.destino else None
    if dest and dest not in permitidos:
        raise PermissionDenied
    acao = request.POST.get("acao", "") if request.method == "POST" else ""
    amostras = bool(request.POST.get("amostras"))

    try:
        if acao == "destino":
            novo = obter_destino(request.POST.get("destino", ""))
            if novo is None or novo not in permitidos:
                raise ValidationError("Escolha um cadastro de destino.")
            imp.destino, imp.status, imp.resultado = novo.chave, "lido", {}
            imp.mapeamento = {c: {"coluna": col} for c, col in sugestao.heuristica_mapeamento(novo, tab).items()}
            imp.opcoes = {k: v for k, v in imp.opcoes.items() if k == "conexao"}
            imp.save()
            return redirect("importacao:mapear", pk=imp.pk)
        if acao == "destino_ia":
            chave, motivo = sugestao.sugerir_destino_ia(tab, amostras)
            imp.usou_ia = True
            imp.observacao_ia = motivo
            imp.save(update_fields=["usou_ia", "observacao_ia"])
            if chave and obter_destino(chave) in permitidos:
                messages.success(request, f"A IA sugere: {obter_destino(chave).rotulo}. {motivo}")
                return redirect(f"{request.path}?sugestao={chave}")
            messages.warning(request, "A IA não encontrou um cadastro adequado. Escolha manualmente.")
            return redirect("importacao:mapear", pk=imp.pk)
        if dest and acao in ("salvar", "ia", "simular"):
            if acao == "ia":
                sugerido, obs = sugestao.sugerir_mapeamento_ia(dest, tab, amostras)
                imp.mapeamento = {c: {"coluna": col} for c, col in sugerido.items()}
                imp.usou_ia, imp.observacao_ia, imp.status, imp.resultado = True, obs, "mapeado", {}
                imp.save()
                messages.success(request, "Mapeamento sugerido pela IA. Confira cada campo antes de simular.")
                return redirect("importacao:mapear", pk=imp.pk)
            novo_map = _mapeamento_do_post(dest, request.POST, tab.colunas)
            imp.mapeamento = novo_map
            imp.opcoes = _opcoes_do_post(request.POST, request.user, imp.opcoes)
            imp.status, imp.resultado = "mapeado", {}
            imp.save()
            faltam = [c.rotulo for c in dest.campos if c.obrigatorio and c.nome not in novo_map]
            if acao == "simular":
                if faltam:
                    raise ValidationError("Falta ligar um campo obrigatório: " + ", ".join(faltam) + ".")
                imp.resultado = servico.executar(imp, request.user, simular=True)
                imp.status = "simulado"
                imp.save()
                return redirect("importacao:detalhe", pk=imp.pk)
            messages.success(request, "Mapeamento salvo.")
            return redirect("importacao:mapear", pk=imp.pk)
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    except PermissionError as e:
        messages.error(request, str(e))
    except Exception as e:                      # IA fora do ar, resposta inválida etc.
        if acao in ("ia", "destino_ia"):
            messages.error(request, f"Não foi possível usar a IA agora: {e}")
        else:
            raise

    ctx = {"imp": imp, "tab": tab, "titulo": "Mapear colunas", "icone": "diagram-3",
           "destinos": permitidos, "ia": sugestao.ia_disponivel(), "sugestao": request.GET.get("sugestao", "")}
    if dest:
        idx = {c: i for i, c in enumerate(tab.colunas)}
        linhas = []
        for c in dest.campos:
            m = imp.mapeamento.get(c.nome, {})
            exemplos = []
            if m.get("coluna") in idx:
                exemplos = [str(l[idx[m["coluna"]]]) for l in tab.linhas[:3] if l[idx[m["coluna"]]] not in (None, "")]
            linhas.append({"campo": c, "coluna": m.get("coluna", ""), "fixo": m.get("fixo", ""), "exemplos": exemplos})
        ctx.update({
            "dest": dest, "linhas": linhas, "previa": servico.previa(imp),
            "opcoes": imp.opcoes, "duplicados": servico.DUPLICADOS, "papeis": servico.PAPEIS,
            "empresas": Empresa.acessiveis(request.user), "na_nuvem": _na_nuvem()})
    else:
        ctx["amostra"] = tab.linhas[:5]
        ctx["na_nuvem"] = _na_nuvem()
    return render(request, "importacao/mapear.html", ctx)


def _na_nuvem():
    from apps.assistente.models import ConfiguracaoIA
    return ConfiguracaoIA.atual().na_nuvem


# --------------------------------------------------------------------------- resultado, importar, desfazer
@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
def detalhe(request, pk):
    imp = _consultavel(request, pk)
    dest = obter_destino(imp.destino) if imp.destino else None
    return render(request, "importacao/detalhe.html", {
        "imp": imp, "dest": dest, "r": imp.resultado or {}, "titulo": f"Importação #{imp.pk}", "icone": "cloud-upload",
        "erros": (imp.resultado or {}).get("erros_lista", [])[:200], "dono": imp.usuario_id == request.user.pk})


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
@require_POST
def importar(request, pk):
    imp = _minha(request, pk)
    if imp.status != "simulado" or not imp.dados:
        messages.error(request, "Simule a importação antes de importar.")
        return redirect("importacao:detalhe", pk=imp.pk)
    try:
        resultado = servico.executar(imp, request.user, simular=False)
    except PermissionError as e:
        messages.error(request, str(e))
        return redirect("importacao:detalhe", pk=imp.pk)
    imp.resultado = resultado
    if resultado["revertida"]:
        imp.status = "recusada"
        messages.error(request, "Havia linhas com erro e a opção \"tudo ou nada\" estava marcada: nada foi importado.")
    else:
        imp.status = "concluida"
        imp.apagar_arquivos()
        messages.success(request, f"Importação concluída: {resultado['criados']} criados, {resultado['atualizados']} "
                                  f"atualizados, {resultado['ignorados']} ignorados, {resultado['erros']} com erro.")
    imp.save()
    return redirect("importacao:detalhe", pk=imp.pk)


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
@require_POST
def desfazer(request, pk):
    imp = _minha(request, pk)
    if imp.status != "concluida" or not imp.criados:
        messages.error(request, "Não há o que desfazer nesta importação.")
        return redirect("importacao:detalhe", pk=imp.pk)
    apagados, mantidos = servico.desfazer(imp, request.user)
    imp.status = "desfeita"
    imp.save()
    texto = f"{apagados} registros apagados."
    if mantidos:
        texto += f" {mantidos} foram mantidos porque já são usados em outros cadastros."
    messages.success(request, texto)
    return redirect("importacao:detalhe", pk=imp.pk)


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
@require_POST
def excluir(request, pk):
    imp = _minha(request, pk)
    imp.apagar_arquivos()
    imp.delete()
    messages.success(request, "Registro da importação excluído (os cadastros importados continuam).")
    return redirect("importacao:lista")


def _seguro(v):
    v = str(v)
    return "'" + v if v[:1] in "=+-@\t\r" else v


@login_required
@permission_required("importacao.importar_dados", raise_exception=True)
def erros_csv(request, pk):
    imp = _consultavel(request, pk)
    resposta = HttpResponse(content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = f'attachment; filename="importacao_{imp.pk}_erros.csv"'
    resposta.write("﻿")
    w = csv.writer(resposta, delimiter=";")
    w.writerow(["linha", "problema"])
    for linha, msg in (imp.resultado or {}).get("erros_lista", []):
        w.writerow([linha, _seguro(msg)])
    return resposta
