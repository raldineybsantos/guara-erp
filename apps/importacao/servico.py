"""Executa uma importação: converte cada linha, resolve relacionamentos (departamento, cargo…), trata duplicados e grava.
Cada linha roda no seu próprio savepoint; a simulação faz tudo e desfaz no fim, então mostra exatamente o que aconteceria."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.core.crud import restringir_por_empresa

from . import conversao as cv
from .destinos import destino as obter_destino
from .leitores import carregar_tabela

DUPLICADOS = [("pular", "Ignorar a linha (manter o que já existe)"),
              ("atualizar", "Atualizar o cadastro existente com os dados preenchidos"),
              ("erro", "Tratar como erro")]
PAPEIS = [("nenhum", "Só o cadastro de parceiro"), ("cliente", "Cliente"), ("fornecedor", "Fornecedor"),
          ("ambos", "Cliente e fornecedor")]
MAX_ERROS = 5000


def _indices(tabela):
    return {c: i for i, c in enumerate(tabela.colunas)}


def valores_da_linha(dest, mapeamento, idx, linha):
    """(valores {campo: valor convertido}, erros [(rótulo, mensagem)]). Colunas vazias não entram em `valores`."""
    valores, erros = {}, []
    for campo in dest.campos:
        m = mapeamento.get(campo.nome) or {}
        if m.get("coluna") in idx:
            bruto = linha[idx[m["coluna"]]]
        elif m.get("fixo") not in (None, ""):
            bruto = m["fixo"]
        else:
            continue
        if cv.vazio(bruto):
            continue
        try:
            valores[campo.nome] = campo.converter(bruto)
        except (ValueError, TypeError) as e:
            erros.append((campo.rotulo, str(e) or "valor inválido"))
    return valores, erros


def previa(imp, n=6):
    """Primeiras linhas já convertidas, para conferir o mapeamento antes de simular."""
    dest = obter_destino(imp.destino)
    tabela = carregar_tabela(imp.dados.path)
    idx = _indices(tabela)
    saida = []
    for i, linha in enumerate(tabela.linhas[:n], start=2):
        valores, erros = valores_da_linha(dest, imp.mapeamento, idx, linha)
        saida.append({"linha": i, "valores": [(c.rotulo, valores.get(c.nome)) for c in dest.campos if c.nome in valores],
                      "erros": erros})
    return saida


def _mensagem(e):
    if isinstance(e, ValidationError):
        if hasattr(e, "message_dict"):
            return "; ".join(f"{', '.join(m)}" if k == "__all__" else f"{k}: {', '.join(m)}"
                             for k, m in e.message_dict.items())
        return "; ".join(e.messages)
    if isinstance(e, IntegrityError):
        return "Conflito com um cadastro já existente."
    return str(e)


class _Execucao:
    def __init__(self, imp, user, empresa):
        self.imp, self.user, self.empresa = imp, user, empresa
        self.dest = obter_destino(imp.destino)
        self.op = imp.opcoes or {}
        self.cache_fk = {}
        self.criados = []
        self.cont = {"criados": 0, "atualizados": 0, "ignorados": 0, "vazias": 0, "erros": 0, "relacionados": 0}
        self.erros = []

    # ---------------------------------------------------------------- relacionamentos
    def _fk(self, nome, texto):
        modelo, busca = self.dest.fks[nome]
        chave = (nome,)
        if chave not in self.cache_fk:
            qs = modelo.objects.all()
            if self.dest.por_empresa and hasattr(modelo, "empresa"):
                qs = restringir_por_empresa(qs, modelo, self.user)
            self.cache_fk[chave] = {cv.normalizar(getattr(o, busca)): o for o in qs}
        achado = self.cache_fk[chave].get(cv.normalizar(texto))
        if achado:
            return achado
        if not self.op.get("criar_relacionados"):
            rotulo = self.dest.por_nome[nome].rotulo
            raise ValidationError({rotulo: f'"{texto}" não está cadastrado. Cadastre antes ou marque a opção de criar '
                                           "automaticamente."})
        extra = {"empresa": self.empresa} if hasattr(modelo, "empresa") and self.empresa else {}
        obj = modelo(**{busca: texto[:150]}, **extra)
        obj.full_clean()
        obj.save()
        self.cache_fk[chave][cv.normalizar(texto)] = obj
        self.criados.append([modelo._meta.label_lower, obj.pk])
        self.cont["relacionados"] += 1
        return obj

    # ---------------------------------------------------------------- uma linha
    def _linha(self, n, linha, idx):
        valores, erros = valores_da_linha(self.dest, self.imp.mapeamento, idx, linha)
        if erros:
            raise ValidationError({r: m for r, m in erros})
        if not valores:
            self.cont["vazias"] += 1
            return
        for nome in self.dest.fks:
            if nome in valores:
                valores[nome] = self._fk(nome, valores[nome]) if valores[nome] else None
        modelo = self.dest.modelo
        existente = None
        for u in self.dest.unicos:
            if valores.get(u):
                existente = modelo.objects.filter(**{u: valores[u]}).first()
                if existente:
                    break
        if existente:
            modo = self.op.get("duplicados", "pular")
            if modo == "erro":
                raise ValidationError("Já existe um cadastro com este valor.")
            if modo == "pular":
                self.cont["ignorados"] += 1
                return
            if not self.dest.pode_atualizar(self.user):
                raise ValidationError("Sem permissão para atualizar cadastros existentes.")
            if not restringir_por_empresa(modelo.objects.filter(pk=existente.pk), modelo, self.user).exists():
                raise ValidationError("O cadastro existente pertence a uma empresa a que você não tem acesso.")
            for k, v in valores.items():
                setattr(existente, k, v)
            existente.full_clean()
            existente.save()
            self.cont["atualizados"] += 1
            return
        obj = modelo(**valores)
        if self.dest.por_empresa and self.empresa and hasattr(obj, "empresa"):
            obj.empresa = self.empresa
        obj.full_clean()
        obj.save()
        self.criados.append([modelo._meta.label_lower, obj.pk])
        if self.dest.papel:
            self._papeis(obj)
        self.cont["criados"] += 1

    def _papeis(self, obj):
        from apps.core.models import Cliente, Fornecedor
        escolha = self.op.get("papel", "nenhum")
        for papel, modelo in (("cliente", Cliente), ("fornecedor", Fornecedor)):
            if escolha in (papel, "ambos"):
                if not modelo.objects.filter(parceironegocio_id=obj.pk).exists():
                    novo = obj.adicionar_papel(papel)
                    self.criados.append([modelo._meta.label_lower, novo.pk])

    # ---------------------------------------------------------------- tudo
    def rodar(self):
        tabela = carregar_tabela(self.imp.dados.path)
        idx = _indices(tabela)
        for n, linha in enumerate(tabela.linhas, start=2):
            antes = (len(self.criados), self.cont["relacionados"])
            try:
                with transaction.atomic():
                    self._linha(n, linha, idx)
            except (ValidationError, IntegrityError, ValueError) as e:
                del self.criados[antes[0]:]                  # o savepoint desfez; o registro local acompanha
                self.cont["relacionados"] = antes[1]
                self.cache_fk.clear()
                self.cont["erros"] += 1
                if len(self.erros) < MAX_ERROS:
                    self.erros.append([n, _mensagem(e)])


def executar(imp, user, simular=True):
    """Roda a importação. Devolve o dicionário de resultado (e, se não for simulação, grava `criados` em imp)."""
    from apps.core.models import Empresa
    dest = obter_destino(imp.destino)
    if dest is None or not dest.pode(user):
        raise PermissionError("Sem permissão para importar neste cadastro.")
    empresa = None
    if (imp.opcoes or {}).get("empresa"):
        empresa = Empresa.acessiveis(user).filter(pk=imp.opcoes["empresa"]).first()
    ex = _Execucao(imp, user, empresa)
    tudo_ou_nada = bool((imp.opcoes or {}).get("tudo_ou_nada"))
    with transaction.atomic():
        ex.rodar()
        desfazer = simular or (tudo_ou_nada and ex.cont["erros"])
        if desfazer:
            transaction.set_rollback(True)
    resultado = {**ex.cont, "erros_lista": ex.erros, "simulacao": simular,
                 "revertida": bool(not simular and tudo_ou_nada and ex.cont["erros"])}
    if not simular and not resultado["revertida"]:
        imp.criados = ex.criados
    return resultado


def desfazer(imp, user):
    """Apaga o que a importação criou (do último para o primeiro). Devolve (apagados, mantidos)."""
    from django.apps import apps as registro
    apagados = mantidos = 0
    for label, pk in reversed(imp.criados or []):
        modelo = registro.get_model(label)
        try:
            with transaction.atomic():
                obj = modelo.objects.filter(pk=pk).first()      # pela instância: respeita as regras de exclusão do modelo
                if obj is not None:
                    obj.delete()
                    apagados += 1
        except (ProtectedError, IntegrityError):
            mantidos += 1
    return apagados, mantidos
