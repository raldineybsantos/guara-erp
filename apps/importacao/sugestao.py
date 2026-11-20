"""Sugestão de destino e de mapeamento de colunas: primeiro por regras (nome e conteúdo da coluna, sem custo e sem
mandar nada para fora), e, a pedido, pela IA configurada no ERP."""
import difflib
import re

from django.core.exceptions import ValidationError

from apps.assistente import ia
from apps.assistente.models import ConfiguracaoIA

from . import conversao as cv
from .destinos import destinos


def _pontos_nome(campo, coluna):
    n = cv.normalizar(coluna)
    if not n:
        return 0
    alvos = {cv.normalizar(campo.nome), cv.normalizar(campo.rotulo), *map(cv.normalizar, campo.sinonimos)}
    if n in alvos:
        return 1.0
    melhor = max((difflib.SequenceMatcher(None, n, a).ratio() for a in alvos if a), default=0)
    if melhor >= 0.84:
        return melhor
    if any(len(a) >= 4 and (a in n or n in a) for a in alvos if a) and len(n) >= 4:
        return 0.72
    return 0


def _pontos_conteudo(campo, valores):
    v = [cv.texto(x) for x in valores if not cv.vazio(x)]
    if len(v) < 1:
        return 0

    def taxa(f):
        return sum(1 for x in v if f(x)) / len(v)
    if campo.nome in ("cpf",) and taxa(lambda x: len(cv.so_digitos(x)) in (10, 11)) >= 0.8:
        return 0.8
    if campo.nome == "documento" and taxa(lambda x: len(cv.so_digitos(x)) in (11, 13, 14)) >= 0.8:
        return 0.75
    if campo.tipo == "email" and taxa(lambda x: re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", x) is not None) >= 0.8:
        return 0.9
    if campo.nome == "cep" and taxa(lambda x: len(cv.so_digitos(x)) == 8) >= 0.8:
        return 0.8
    return 0


def heuristica_mapeamento(destino, tabela):
    """{campo: coluna} pelas melhores correspondências (cada coluna e cada campo usados no máximo uma vez)."""
    amostra = {c: [l[i] for l in tabela.linhas[:30]] for i, c in enumerate(tabela.colunas)}
    pares = []
    for campo in destino.campos:
        for col in tabela.colunas:
            p = max(_pontos_nome(campo, col), _pontos_conteudo(campo, amostra[col]))
            if p >= 0.7:
                pares.append((p, campo.nome, col))
    pares.sort(key=lambda x: -x[0])
    usados_campos, usadas_cols, saida = set(), set(), {}
    for p, campo, col in pares:
        if campo in usados_campos or col in usadas_cols:
            continue
        saida[campo] = col
        usados_campos.add(campo)
        usadas_cols.add(col)
    return saida


def heuristica_destino(tabela):
    """Destino com mais campos reconhecidos nas colunas (mínimo de 2)."""
    melhor, pontos = None, 1
    for d in destinos().values():
        n = len(heuristica_mapeamento(d, tabela))
        if n > pontos:
            melhor, pontos = d.chave, n
    return melhor


# --------------------------------------------------------------------------- IA
def _colunas_para_ia(tabela, enviar_amostras):
    saida = []
    for i, c in enumerate(tabela.colunas[:80]):
        item = {"nome": c}
        if enviar_amostras:
            ex = []
            for l in tabela.linhas[:40]:
                v = cv.texto(l[i])
                if v and v not in ex:
                    ex.append(v[:40])
                if len(ex) == 3:
                    break
            item["exemplos"] = ex
        saida.append(item)
    return saida


def ia_disponivel():
    return ConfiguracaoIA.atual().provedor != "desligado"


def sugerir_mapeamento_ia(destino, tabela, enviar_amostras=False):
    """(mapeamento {campo: coluna}, observações). Só aceita campos e colunas que existem de fato."""
    import json
    config = ConfiguracaoIA.atual()
    sistema = ("Você ajuda a migrar dados de planilhas e bancos de outros sistemas para o Guará ERP. Receberá os campos "
               "do cadastro de destino e as colunas da origem. Responda SOMENTE um objeto JSON no formato "
               '{"mapeamento": {"<campo do Guará>": "<nome exato da coluna de origem>"}, "observacoes": "<texto curto>"}. '
               "Use só campos e colunas da lista, cada coluna no máximo uma vez, e omita o campo que não tiver "
               "correspondência clara. Em observacoes, avise o que exige atenção (ex.: coluna com dois dados juntos).")
    usuario = json.dumps({"cadastro_destino": destino.rotulo, "campos": [c.descricao_ia() for c in destino.campos],
                          "tabela_origem": tabela.nome, "colunas": _colunas_para_ia(tabela, enviar_amostras)},
                         ensure_ascii=False)
    r = ia.extrair_json(ia.chamar(config, sistema, usuario))
    bruto = r.get("mapeamento") if isinstance(r.get("mapeamento"), dict) else {}
    campos, cols, saida, usadas = set(destino.por_nome), set(tabela.colunas), {}, set()
    for campo, col in bruto.items():
        if campo in campos and isinstance(col, str) and col in cols and col not in usadas:
            saida[campo] = col
            usadas.add(col)
    if not saida:
        raise ValidationError("A IA não encontrou correspondências para esta tabela.")
    return saida, str(r.get("observacoes") or "")[:600]


def sugerir_destino_ia(tabela, enviar_amostras=False):
    import json
    config = ConfiguracaoIA.atual()
    ds = destinos()
    sistema = ("Você ajuda a migrar dados para o Guará ERP. Diga para qual cadastro de destino a tabela de origem "
               'deve ir. Responda SOMENTE JSON: {"destino": "<chave>", "motivo": "<texto curto>"}; use "nenhum" se '
               "nenhum servir.")
    usuario = json.dumps({"destinos": [{"chave": d.chave, "rotulo": d.rotulo, "campos": [c.rotulo for c in d.campos][:25]}
                                       for d in ds.values()],
                          "tabela_origem": tabela.nome, "colunas": _colunas_para_ia(tabela, enviar_amostras)},
                         ensure_ascii=False)
    r = ia.extrair_json(ia.chamar(config, sistema, usuario))
    chave = r.get("destino")
    return (chave if chave in ds else None), str(r.get("motivo") or "")[:300]
