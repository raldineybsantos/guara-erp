"""Popula o banco com dados fictícios para demonstração e testes."""
import datetime
import random
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


def gerar_cpf(rnd):
    n = [rnd.randint(0, 9) for _ in range(9)]
    for i in (9, 10):
        s = sum(n[j] * ((i + 1) - j) for j in range(i))
        n.append((s * 10) % 11 % 10)
    return "".join(map(str, n))


def gerar_cnpj(rnd):
    n = [rnd.randint(0, 9) for _ in range(8)] + [0, 0, 0, 1]
    for pesos in ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]):
        r = sum(a * b for a, b in zip(n, pesos)) % 11
        n.append(0 if r < 2 else 11 - r)
    return "".join(map(str, n))


class Command(BaseCommand):
    help = "Cria dados fictícios (empresa, colaboradores, títulos, notas, processos) para demonstração."

    def add_arguments(self, parser):
        parser.add_argument("--senha", default="demo1234", help="Senha dos usuários de demonstração")

    @transaction.atomic
    def handle(self, *args, senha, **options):
        from apps.cobranca.models import Cobranca
        from apps.core.models import Cargo, Cliente, Colaborador, Departamento, Empresa, Fornecedor
        from apps.financeiro.models import CentroCusto, ContaBancaria, ContaPagar, ContaReceber, Movimentacao, PlanoConta
        from apps.fiscal.models import ItemNota, NotaFiscal, Produto
        from apps.workflow.models import Processo

        if Empresa.objects.exists():
            self.stdout.write(self.style.WARNING("Já existem dados; seed ignorado."))
            return
        call_command("setup_erp", verbosity=0)
        rnd = random.Random(42)
        hoje = timezone.localdate()

        emp = Empresa.objects.create(razao_social="Empresa Exemplo Ltda", nome_fantasia="Exemplo",
                                     cnpj=gerar_cnpj(rnd), cidade="São Paulo", uf="SP",
                                     chave_pix="financeiro@exemplo.com.br", email="contato@exemplo.com.br")

        deps = {n: Departamento.objects.create(nome=n, codigo=c) for c, n in
                [("ADM", "Administrativo"), ("FIN", "Financeiro"), ("COM", "Comercial"), ("TI", "Tecnologia"),
                 ("RH", "Recursos Humanos"), ("OPE", "Operações")]}
        cargos = [Cargo.objects.create(nome=n, salario_base=Decimal(s)) for n, s in
                  [("Analista", 5200), ("Assistente", 3100), ("Coordenador", 8900), ("Diretor", 18000),
                   ("Desenvolvedor", 9500), ("Vendedor", 3800)]]
        nomes = ["Ana Souza", "Bruno Lima", "Carla Mendes", "Diego Alves", "Eduarda Rocha", "Felipe Costa",
                 "Gabriela Nunes", "Henrique Dias", "Isabela Martins", "João Pereira", "Larissa Gomes",
                 "Marcos Ribeiro", "Natália Freitas", "Otávio Barros", "Paula Teixeira"]
        for i, nome in enumerate(nomes, 1):
            cargo = rnd.choice(cargos)
            Colaborador.objects.create(matricula=f"{i:04d}", nome=nome, cpf=gerar_cpf(rnd), cargo=cargo,
                                       departamento=rnd.choice(list(deps.values())), salario=cargo.salario_base,
                                       email=f"{nome.split()[0].lower()}@exemplo.com.br",
                                       data_admissao=hoje - datetime.timedelta(days=rnd.randint(30, 2000)))

        forns = [Fornecedor.objects.create(nome=n, documento=gerar_cnpj(rnd), categoria=c, email=f"contato{i}@fornecedor.com")
                 for i, (n, c) in enumerate([("Papelaria Central Ltda", "Material de escritório"),
                                             ("Nuvem Hosting S.A.", "Software e TI"),
                                             ("Energia Paulista", "Utilidades"),
                                             ("Imobiliária Horizonte", "Aluguel"),
                                             ("Distribuidora Alfa", "Mercadorias")])]
        clis = [Cliente.objects.create(nome=n, documento=gerar_cnpj(rnd), email=f"financeiro{i}@cliente.com")
                for i, n in enumerate(["Mercado Bom Preço", "Construtora Pilar", "Clínica Vida", "Escola Saber",
                                       "Restaurante Sabor", "Transportes Rápido"])]

        banco = ContaBancaria.objects.create(nome="Banco do Brasil CC", banco="Banco do Brasil", codigo_banco="001",
                                             agencia="1234", conta="56789-0", saldo_inicial=Decimal("85000"),
                                             data_saldo_inicial=hoje - datetime.timedelta(days=200))
        caixa = ContaBancaria.objects.create(nome="Caixa interno", tipo="caixa", saldo_inicial=Decimal("2000"))
        cc = [CentroCusto.objects.create(codigo=d.codigo, nome=d.nome, departamento=d) for d in deps.values()]
        pc = {p.codigo: p for p in PlanoConta.objects.all()}

        # títulos dos últimos 5 meses e próximos 2
        for mes in range(-5, 3):
            m = hoje.month - 1 + mes
            base = datetime.date(hoje.year + m // 12, m % 12 + 1, 1)
            for forn, cod, valor in ((forns[3], "2.02.01", 7500), (forns[2], "2.02.02", 1300),
                                     (forns[1], "2.02.03", 2400), (forns[4], "2.03", rnd.randint(8000, 20000))):
                cp = ContaPagar.objects.create(fornecedor=forn, descricao=f"{forn.categoria} {base:%m/%Y}",
                                               valor=Decimal(valor + rnd.randint(0, 300)), plano_conta=pc[cod],
                                               centro_custo=rnd.choice(cc), vencimento=base.replace(day=rnd.randint(5, 25)),
                                               documento=str(rnd.randint(1000, 9999)))
                if cp.vencimento < hoje - datetime.timedelta(days=3):
                    cp.baixar(cp.saldo, cp.vencimento, banco, "boleto")
            for cli in rnd.sample(clis, 4):
                cr = ContaReceber.objects.create(cliente=cli, descricao=f"Serviços {base:%m/%Y}",
                                                 valor=Decimal(rnd.randint(4000, 16000)), plano_conta=pc["1.02"],
                                                 vencimento=base.replace(day=rnd.randint(5, 28)),
                                                 documento=str(rnd.randint(100, 999)))
                if cr.vencimento < hoje - datetime.timedelta(days=5) and rnd.random() > 0.15:
                    cr.baixar(cr.saldo, cr.vencimento, banco, "pix")
                elif cr.vencimento >= hoje:
                    Cobranca.objects.create(conta_receber=cr, tipo="pix", valor=cr.saldo, vencimento=cr.vencimento)
        Movimentacao.objects.create(conta_bancaria=caixa, tipo="saida", valor=Decimal("180"), descricao="Café e copa",
                                    plano_conta=pc["2.09"])

        prods = [Produto.objects.create(codigo=c, descricao=d, tipo=t, ncm=n, cfop_padrao=cf, preco=Decimal(p),
                                        aliquota_icms=Decimal(ic), aliquota_pis=Decimal("0.65"),
                                        aliquota_cofins=Decimal("3.00"), aliquota_iss=Decimal(iss))
                 for c, d, t, n, cf, p, ic, iss in [
                     ("SRV001", "Consultoria em TI (hora)", "servico", "", "", 180, 0, 5),
                     ("SRV002", "Suporte mensal", "servico", "", "", 2500, 0, 5),
                     ("PRD001", "Notebook 14\"", "produto", "84713012", "5102", 4200, 18, 0),
                     ("PRD002", "Monitor 24\"", "produto", "85285200", "5102", 950, 18, 0)]]
        for i in range(1, 6):
            nf = NotaFiscal.objects.create(tipo="saida", modelo="55", cliente=rnd.choice(clis),
                                           data_emissao=hoje - datetime.timedelta(days=rnd.randint(1, 120)))
            for p in rnd.sample(prods[2:], 2):
                ItemNota.objects.create(nota=nf, produto=p, quantidade=Decimal(rnd.randint(1, 5)))
            nf.recalcular()
            nf.emitir()

        # usuários de demonstração por perfil
        for username, grupo, nome in (("financeiro", "Financeiro", "Fernanda Financeiro"), ("rh", "RH", "Roberto RH"),
                                      ("fiscal", "Fiscal", "Fábio Fiscal"), ("diretor", "Administradores", "Diana Diretora")):
            u, criado = User.objects.get_or_create(username=username, defaults={
                "first_name": nome.split()[0], "last_name": nome.split()[1], "email": f"{username}@exemplo.com.br"})
            if criado:
                u.set_password(senha)
                u.save()
            u.groups.add(Group.objects.get(name=grupo))

        admin = User.objects.filter(is_superuser=True).first() or User.objects.get(username="diretor")
        proc = Processo.objects.get(codigo="aprovacao-pagamento")
        for cp in ContaPagar.objects.em_aberto().filter(vencimento__gte=hoje)[:3]:
            proc.iniciar(f"Aprovar pagamento: {cp}", admin, f"Fornecedor: {cp.fornecedor}", objeto=cp)

        self.stdout.write(self.style.SUCCESS(
            f"Dados de demonstração criados. Usuários: financeiro, rh, fiscal, diretor (senha: {senha})."))
