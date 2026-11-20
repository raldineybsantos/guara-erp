"""SPED Contábil (ECD): gera o arquivo texto do Livro Diário completo (livro G) a partir do razão único.

Blocos: 0 (abertura), I (plano de contas, balancetes mensais, diário, apuração do resultado), J (balanço, DRE,
termo de encerramento e signatários) e 9 (controle). O leiaute da ECD muda de ano para ano: **o arquivo tem de
ser conferido no PVA da Receita antes de ser assinado e transmitido**. Fica de fora o mapeamento ao plano de contas
referencial (I051), exigido de algumas empresas."""
import datetime
from collections import OrderedDict
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.core.models import Empresa
from apps.core.validators import somente_digitos

from . import servicos
from .encerramento import RESULTADO, encerramento_do_ano
from .models import ContaContabil, Lancamento, Partida

ZERO = Decimal("0")
VERSAO_LEIAUTE = "9.00"   # COD_VER_LC; confira a versão vigente no manual da ECD
NATUREZA_SPED = {"ativo": "01", "passivo": "02", "pl": "03", "receita": "04", "deducao": "04", "custo": "04",
                 "despesa": "04"}


def _txt(v, limite=None):
    v = "" if v is None else str(v)
    v = v.replace("|", " ").replace("\r", " ").replace("\n", " ").strip()
    return v[:limite] if limite else v


def _data(d):
    return d.strftime("%d%m%Y") if d else ""


def _valor(v):
    return f"{abs(Decimal(v)):.2f}".replace(".", ",")


def _dc(v, devedora_se_positivo=True):
    """'D' ou 'C' conforme o sinal do saldo devedor (positivo = devedor)."""
    return "D" if Decimal(v) >= 0 else "C"


def _niveis(contas):
    """Contas em ordem de árvore (pai antes dos filhos) com o nível (1 = raiz)."""
    por_pai = {}
    for c in contas:
        por_pai.setdefault(c.superior_id, []).append(c)
    ordem = []

    def descer(pai_id, nivel):
        for c in sorted(por_pai.get(pai_id, []), key=lambda x: [int(p) if p.isdigit() else 0 for p in x.codigo.split(".")]):
            ordem.append((c, nivel))
            descer(c.pk, nivel + 1)
    descer(None, 1)
    return ordem


def _somar_arvore(contas, valores):
    """Soma o saldo devedor das analíticas nas sintéticas ascendentes. valores: {conta_id: saldo devedor}."""
    por_id = {c.pk: c for c in contas}
    total = {c.pk: valores.get(c.pk, ZERO) for c in contas}
    for c in contas:
        if c.sintetica:
            continue
        pai = por_id.get(c.superior_id)
        v = valores.get(c.pk, ZERO)
        while pai is not None and v:
            total[pai.pk] += v
            pai = por_id.get(pai.superior_id)
    return total


def _saldo_dev(mapa):
    return {pk: d - c for pk, (d, c) in mapa.items()}


class Gerador:
    def __init__(self, empresa, ano, signatarios, num_ordem=1, natureza_livro="DIÁRIO GERAL"):
        self.empresa, self.ano = empresa, ano
        self.signatarios = signatarios
        self.num_ordem = num_ordem
        self.natureza_livro = natureza_livro
        self.ini, self.fim = datetime.date(ano, 1, 1), datetime.date(ano, 12, 31)
        self.blocos = OrderedDict((b, []) for b in ("0", "I", "J"))
        self.contas = list(ContaContabil.objects.all())
        self.arvore = _niveis(self.contas)
        self.cod = {c.pk: c.codigo for c in self.contas}

    # -------------------------------------------------------------- validação
    def validar(self):
        e = self.empresa
        erros = []
        if len(somente_digitos(e.cnpj)) != 14:
            erros.append("CNPJ da empresa inválido.")
        if not e.uf:
            erros.append("Informe a UF da empresa.")
        if not e.codigo_municipio:
            erros.append("Informe o código IBGE do município da empresa.")
        if not encerramento_do_ano(self.ano, e):
            erros.append(f"Encerre o exercício {self.ano} antes (Contabilidade → Encerrar exercício).")
        anterior = servicos.saldos(ate=self.ini - datetime.timedelta(days=1), empresa=e)
        pend = [self.cod[pk] for pk, (d, c) in anterior.items()
                if d != c and next(x for x in self.contas if x.pk == pk).natureza in RESULTADO]
        if pend:
            erros.append("Há saldo de resultado de exercícios anteriores sem encerramento (contas "
                         f"{', '.join(sorted(pend)[:5])}). Encerre os exercícios anteriores primeiro.")
        if not any(s.get("nome") for s in self.signatarios):
            erros.append("Informe ao menos um signatário (contador ou representante legal).")
        for s in self.signatarios:
            if s.get("nome") and len(somente_digitos(s.get("cpf", ""))) not in (11, 14):
                erros.append(f"CPF/CNPJ do signatário {s['nome']} inválido.")
        lancs = Lancamento.objects.filter(empresa=e, data__gte=self.ini, data__lte=self.fim)
        if not lancs.exists():
            erros.append(f"Não há lançamentos de {self.ano} para a empresa.")
        for l in lancs.prefetch_related("partidas"):
            d = sum((p.valor for p in l.partidas.all() if p.tipo == "D"), ZERO)
            c = sum((p.valor for p in l.partidas.all() if p.tipo == "C"), ZERO)
            if d != c:
                erros.append(f"Lançamento {l.numero} desbalanceado.")
                break
        if erros:
            raise ValidationError(erros)

    # -------------------------------------------------------------- registros
    def reg(self, bloco, *campos):
        self.blocos[bloco].append("|" + "|".join(_txt(c) for c in campos) + "|")

    def bloco_0(self):
        e = self.empresa
        self.reg("0", "0000", "LECD", _data(self.ini), _data(self.fim), _txt(e.razao_social, 100),
                 somente_digitos(e.cnpj), e.uf, _txt(e.inscricao_estadual), e.codigo_municipio,
                 _txt(e.inscricao_municipal), "", "0", "0", "0", "", "", "0", "0", "", "N", "N", "0", "0", "")
        self.reg("0", "0001", "0")

    def bloco_i(self):
        e = self.empresa
        self.reg("I", "I001", "0")
        self.reg("I", "I010", "G", VERSAO_LEIAUTE)
        self.reg("I", "I030", "TERMO DE ABERTURA", self.num_ordem, self.natureza_livro, "{TOT}",
                 _txt(e.razao_social, 100), "", somente_digitos(e.cnpj), _data(self.ini), "", _txt(e.cidade, 60),
                 _data(self.fim))
        # plano de contas
        for c, nivel in self.arvore:
            sup = self.cod.get(c.superior_id, "")
            self.reg("I", "I050", _data(self.ini), NATUREZA_SPED[c.natureza], "S" if c.sintetica else "A", nivel,
                     c.codigo, sup, _txt(c.nome, 60))
        # balancetes mensais
        for mes in range(1, 13):
            m_ini = datetime.date(self.ano, mes, 1)
            m_fim = (m_ini.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
            ant = _saldo_dev(servicos.saldos(ate=m_ini - datetime.timedelta(days=1), empresa=self.empresa,
                                             sem_encerramento=self.ano))
            mov = servicos.saldos(de=m_ini, ate=m_fim, empresa=self.empresa, sem_encerramento=self.ano)
            deb = {pk: d for pk, (d, c) in mov.items()}
            cre = {pk: c for pk, (d, c) in mov.items()}
            t_ant = _somar_arvore(self.contas, ant)
            t_deb = _somar_arvore(self.contas, deb)
            t_cre = _somar_arvore(self.contas, cre)
            self.reg("I", "I150", _data(m_ini), _data(m_fim))
            for c, _ in self.arvore:
                a, d, cr = t_ant[c.pk], t_deb[c.pk], t_cre[c.pk]
                fin = a + d - cr
                if not (a or d or cr or fin):
                    continue
                self.reg("I", "I155", c.codigo, "", _valor(a), _dc(a), _valor(d), _valor(cr), _valor(fin), _dc(fin))
        # diário
        lancs = (Lancamento.objects.filter(empresa=self.empresa, data__gte=self.ini, data__lte=self.fim)
                 .order_by("data", "id").prefetch_related("partidas"))
        for l in lancs:
            partidas = list(l.partidas.all())
            total = sum((p.valor for p in partidas if p.tipo == "D"), ZERO)
            self.reg("I", "I200", l.numero, _data(l.data), _valor(total), "E" if l.evento == "encerramento_exercicio" else "N", "")
            for p in partidas:
                self.reg("I", "I250", self.cod[p.conta_id], "", _valor(p.valor), p.tipo, "", "", _txt(l.historico, 250), "")
        # apuração do resultado (saldos das contas de resultado antes do encerramento)
        pre = servicos.saldos(de=self.ini, ate=self.fim, empresa=self.empresa, sem_encerramento=self.ano)
        linhas = [(c, pre[c.pk][0] - pre[c.pk][1]) for c in self.contas
                  if c.natureza in RESULTADO and not c.sintetica and c.pk in pre and pre[c.pk][0] != pre[c.pk][1]]
        if linhas:
            self.reg("I", "I350", _data(self.fim))
            for c, v in sorted(linhas, key=lambda x: x[0].codigo):
                self.reg("I", "I355", c.codigo, "", _valor(v), _dc(v))

    def bloco_j(self):
        e = self.empresa
        self.reg("J", "J001", "0")
        # --- balanço patrimonial
        self.reg("J", "J005", _data(self.ini), _data(self.fim), "1", "BALANÇO PATRIMONIAL")
        ini = _saldo_dev(servicos.saldos(ate=self.ini - datetime.timedelta(days=1), empresa=e))
        fin = _saldo_dev(servicos.saldos(ate=self.fim, empresa=e))
        t_ini, t_fin = _somar_arvore(self.contas, ini), _somar_arvore(self.contas, fin)
        for c, nivel in self.arvore:
            if c.natureza in RESULTADO:
                continue
            a, b = t_ini[c.pk], t_fin[c.pk]
            if not (a or b):
                continue
            grupo = "A" if c.natureza == "ativo" else "P"
            sup = self.cod.get(c.superior_id, "")
            self.reg("J", "J100", c.codigo, "T" if c.sintetica else "D", nivel, sup, grupo, _txt(c.nome, 100),
                     _valor(a), _dc(a), _valor(b), _dc(b), "")
        # --- DRE (movimento do ano antes do encerramento)
        self.reg("J", "J005", _data(self.ini), _data(self.fim), "2", "DEMONSTRAÇÃO DO RESULTADO DO EXERCÍCIO")
        mov = _saldo_dev(servicos.saldos(de=self.ini, ate=self.fim, empresa=e, sem_encerramento=self.ano))
        t_mov = _somar_arvore(self.contas, mov)
        ordem = 0
        for c, nivel in self.arvore:
            if c.natureza not in RESULTADO:
                continue
            v = t_mov[c.pk]
            if not v:
                continue
            ordem += 1
            sup = self.cod.get(c.superior_id, "")
            self.reg("J", "J150", ordem, c.codigo, "T" if c.sintetica else "D", nivel, sup, _txt(c.nome, 100),
                     "0,00", "D", _valor(v), _dc(v), "R" if c.natureza == "receita" else "D", "")

    def bloco_j_final(self):
        e = self.empresa
        self.reg("J", "J900", "TERMO DE ENCERRAMENTO", self.num_ordem, self.natureza_livro, _txt(e.razao_social, 100),
                 "{TOT}", _data(self.ini), _data(self.fim))
        for s in self.signatarios:
            if not s.get("nome"):
                continue
            self.reg("J", "J930", _txt(s["nome"], 100), somente_digitos(s.get("cpf", "")), _txt(s.get("qualificacao"), 100),
                     s.get("codigo", "900"), "0" if s.get("crc") else "", _txt(s.get("email")), "", _txt(s.get("uf_crc")),
                     _txt(s.get("crc")), _data(s.get("data_crc")), "S" if s.get("responsavel_legal") else "N")

    # -------------------------------------------------------------- arquivo
    def gerar(self):
        self.validar()
        self.bloco_0()
        self.bloco_i()
        self.bloco_j()
        self.bloco_j_final()
        # linhas de totalização de cada bloco (a última de cada um)
        for b, reg in (("0", "0990"), ("I", "I990"), ("J", "J990")):
            self.blocos[b].append(f"|{reg}|{len(self.blocos[b]) + 1}|")
        corpo = self.blocos["0"] + self.blocos["I"] + self.blocos["J"]
        contagem = OrderedDict()
        for linha in corpo:
            tipo = linha.split("|")[1]
            contagem[tipo] = contagem.get(tipo, 0) + 1
        tipos = list(contagem) + ["9001", "9900", "9990", "9999"]
        contagem.update({"9001": 1, "9900": len(tipos), "9990": 1, "9999": 1})
        bloco9 = ["|9001|0|"] + [f"|9900|{t}|{contagem[t]}|" for t in tipos]
        bloco9.append(f"|9990|{len(bloco9) + 2}|")
        total = len(corpo) + len(bloco9) + 1
        bloco9.append(f"|9999|{total}|")
        linhas = [l.replace("{TOT}", str(total)) for l in corpo + bloco9]
        return "\r\n".join(linhas) + "\r\n"

    def nome_arquivo(self):
        return f"ECD_{somente_digitos(self.empresa.cnpj)}_{self.ano}0101_{self.ano}1231.txt"


def gerar_ecd(empresa, ano, signatarios, num_ordem=1):
    g = Gerador(empresa, ano, signatarios, num_ordem)
    texto = g.gerar()
    return g.nome_arquivo(), texto.encode("latin-1", "replace")
