"""Dados iniciais do RH: tabelas legais de 2026, rubricas padrão e uma jornada. Só cria o que não existe,
nunca sobrescreve o que o RH/contabilidade já ajustou."""
import datetime
from decimal import Decimal as D

from .models import FaixaINSS, FaixaIRRF, Jornada, ParametroFolha, Rubrica

# Fonte: Portaria Interministerial MPS/MF nº 13/2026 (INSS, salário-família); Receita Federal, Lei 15.270/2025 (IRRF).
INSS_2026 = [("1621.00", "7.5"), ("2902.84", "9"), ("4354.27", "12"), ("8475.55", "14")]
IRRF_2026 = [("2428.80", "0", "0"), ("2826.65", "7.5", "182.16"), ("3751.05", "15", "394.16"),
             ("4664.68", "22.5", "675.49"), (None, "27.5", "908.73")]

CODIGOS_DO_SISTEMA = {"0001", "0010", "0011", "0012", "0013", "0020", "0030", "0031", "0100", "0200", "9003",
                      "9010", "9101", "9300"}

RUBRICAS = [  # código, nome, tipo, INSS, IRRF, FGTS, natureza eSocial (Tabela 3; o RH completa as em branco)
    ("0001", "Salário / pró-labore / bolsa", "provento", True, True, True, "1000"),
    ("0010", "Férias gozadas", "provento", True, True, True, ""),
    ("0011", "1/3 constitucional de férias", "provento", True, True, True, ""),
    ("0012", "Abono pecuniário", "provento", False, False, False, ""),
    ("0013", "1/3 sobre abono pecuniário", "provento", False, False, False, ""),
    ("0020", "Salário-família", "provento", False, False, False, ""),
    ("0030", "13º salário — 1ª parcela", "provento", False, False, False, ""),
    ("0031", "13º salário", "provento", True, True, True, ""),
    ("9003", "Pensão alimentícia", "desconto", False, False, False, ""),
    ("9010", "Desconto de vale-transporte", "desconto", False, False, False, ""),
    ("9101", "Adiantamento de 13º salário", "desconto", False, False, False, ""),
    ("9300", "Desconto de benefícios", "desconto", False, False, False, ""),
    ("0100", "Horas extras", "provento", True, True, True, ""),
    ("0110", "Adicional noturno", "provento", True, True, True, ""),
    ("0120", "Adicional de insalubridade/periculosidade", "provento", True, True, True, ""),
    ("0130", "Comissões", "provento", True, True, True, ""),
    ("0140", "Gratificação", "provento", True, True, True, ""),
    ("0150", "Ajuda de custo / reembolso", "provento", False, False, False, ""),
    ("0200", "Faltas e atrasos", "desconto", True, True, True, ""),
    ("9200", "Adiantamento salarial", "desconto", False, False, False, ""),
    ("9210", "Descontos diversos", "desconto", False, False, False, ""),
]


def carregar():
    if not ParametroFolha.objects.exists():
        p = ParametroFolha.objects.create(
            vigencia_inicio=datetime.date(2026, 1, 1), descricao="Tabelas de 2026",
            salario_minimo=D("1621.00"), deducao_dependente=D("189.59"), desconto_simplificado=D("607.20"),
            reducao_ate=D("5000.00"), reducao_maxima=D("312.89"), reducao_limite=D("7350.00"),
            reducao_fixo=D("978.62"), reducao_fator=D("0.133145"),
            familia_limite=D("1980.38"), familia_cota=D("67.54"))
        for ate, aliq in INSS_2026:
            FaixaINSS.objects.create(parametro=p, ate=D(ate), aliquota=D(aliq))
        for ate, aliq, ded in IRRF_2026:
            FaixaIRRF.objects.create(parametro=p, ate=D(ate) if ate else None, aliquota=D(aliq), deduzir=D(ded))
    for codigo, nome, tipo, inss, irrf, fgts, nat in RUBRICAS:
        Rubrica.objects.get_or_create(codigo=codigo, defaults=dict(nome=nome, tipo=tipo, incide_inss=inss,
                                                                  incide_irrf=irrf, incide_fgts=fgts,
                                                                  natureza_esocial=nat, sistema=codigo in CODIGOS_DO_SISTEMA))
    Jornada.objects.get_or_create(nome="Comercial 40h (seg–sex, 8h)", defaults=dict(
        horas_dia=D("8"), dias_semana="0,1,2,3,4", intervalo_min=60, tolerancia_min=10, divisor_horas=D("200")))
