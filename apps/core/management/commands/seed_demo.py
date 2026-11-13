"""Popula o banco com dados fictícios para demonstração e testes."""
import datetime
import random
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
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
        # Parceiro com dois papéis: a transportadora compra de nós e também presta frete.
        clis[-1].adicionar_papel("fornecedor", categoria="Frete")

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
        usuarios = {}
        for username, grupo, nome, cpf, depto in (
                ("diretor", "Administradores", "Diana Diretora", "39053344705", "ADM"),
                ("financeiro", "Financeiro", "Fernanda Financeiro", "52998224725", "FIN"),
                ("rh", "RH", "Roberto RH", "11144477735", "RH"),
                ("fiscal", "Fiscal", "Fábio Fiscal", "93541134780", "FIN"),
                ("juridico", "Jurídico", "Júlia Jurídico", "15350946056", "ADM"),
                ("compras", "Compras", "Carlos Compras", "12345678062", "ADM"),
                ("coordenador", "Usuários", "Cora Coordenadora", "23456789092", "ADM"),
                ("gerente", "Usuários", "Gustavo Gerente", "23456789173", "ADM")):
            u, criado = User.objects.get_or_create(username=username, defaults={
                "first_name": nome.split()[0], "last_name": nome.split()[1], "email": f"{username}@exemplo.com.br"})
            if criado:
                u.set_password(senha)
                u.save()
            u.groups.add(Group.objects.get(name=grupo))
            dep = Departamento.objects.get(codigo=depto)
            Colaborador.objects.update_or_create(cpf=cpf, defaults=dict(
                nome=nome, usuario=u, departamento=dep, cargo=cargos[2], email=u.email, matricula=f"9{len(usuarios):03d}"))
            usuarios[username] = u
        # estrutura de gestão: a diretora responde pelo Administrativo e é gestora direta dos demais
        diana = Colaborador.objects.get(usuario=usuarios["diretor"])
        Departamento.objects.filter(codigo="ADM").update(responsavel=diana)
        Colaborador.objects.filter(usuario__in=[usuarios[k] for k in ("financeiro", "rh", "fiscal", "juridico", "compras")]
                                   ).update(gestor=diana)
        usuarios["juridico"].user_permissions.add(*Permission.objects.filter(codename="assinar_ecnpj"))

        # quadro de tarefas
        from apps.tarefas.models import Atividade
        g = {n: Group.objects.get(name=n) for n in ("Financeiro", "RH", "Fiscal", "Jurídico")}
        for titulo, status, prio, resp, grupo, dias in (
                ("Conciliar extrato bancário de outubro", "fazendo", "alta", "financeiro", "Financeiro", 2),
                ("Cobrar clientes com títulos vencidos", "a_fazer", "urgente", "financeiro", "Financeiro", -1),
                ("Revisar plano de contas 2027", "a_fazer", "media", None, "Financeiro", 20),
                ("Integração do novo analista", "revisao", "media", "rh", "RH", 5),
                ("Atualizar política de férias", "a_fazer", "baixa", None, "RH", 30),
                ("Conferir notas de entrada da semana", "fazendo", "alta", "fiscal", "Fiscal", 1),
                ("Apuração do ICMS do mês", "a_fazer", "alta", "fiscal", "Fiscal", 8),
                ("Revisar minuta de NDA com cliente", "revisao", "alta", "juridico", "Jurídico", 3),
                ("Fechamento contábil de setembro", "concluida", "media", "financeiro", "Financeiro", -20)):
            a = Atividade.objects.create(titulo=titulo, status=status, prioridade=prio, grupo=g[grupo],
                                         responsavel=usuarios.get(resp) if resp else None,
                                         prazo=hoje + datetime.timedelta(days=dias), criado_por=usuarios["diretor"],
                                         etiquetas=grupo.lower())
            a.mudar_status(status)
            a.save()

        # contratos
        from apps.core.pdf_simples import gerar_pdf
        from django.core.files.base import ContentFile
        from apps.juridico.models import Contrato, Evento, somar_meses
        from apps.juridico.telas import pasta_juridico
        from apps.ged.models import Documento
        pasta = pasta_juridico()
        for titulo, forn, cli, cat, inicio_meses, duracao, mensal, renova, status in (
                ("Locação da sede administrativa", forns[3], None, "locacao", -22, 24, 7500, True, "vigente"),
                ("Hospedagem em nuvem e backup", forns[1], None, "software", -11, 12, 2400, False, "vigente"),
                ("Fornecimento de energia (mercado livre)", forns[2], None, "fornecimento", -6, 36, 1300, False, "vigente"),
                ("Suporte mensal de TI", None, clis[2], "servicos", -1, 12, 2500, True, "aprovado"),
                ("Confidencialidade (NDA) — projeto escola", None, clis[3], "nda", 0, 24, 0, False, "rascunho")):
            inicio = somar_meses(hoje, inicio_meses)
            fim = somar_meses(inicio, duracao) - datetime.timedelta(days=1)
            if titulo.startswith("Hospedagem"):
                fim = hoje + datetime.timedelta(days=25)  # vence em breve: aparece nos alertas
            c = Contrato.objects.create(
                titulo=titulo, tipo="fornecedor" if forn else "cliente", categoria=cat, fornecedor=forn, cliente=cli,
                objeto=f"{titulo} conforme proposta comercial.", valor_mensal=Decimal(mensal),
                valor_total=Decimal(mensal) * duracao, indice_reajuste="ipca" if mensal else "", inicio_vigencia=inicio,
                fim_vigencia=fim, renovacao_automatica=renova, responsavel=usuarios["juridico"],
                departamento=Departamento.objects.get(codigo="ADM"), criado_por=usuarios["juridico"],
                data_assinatura=inicio if status == "vigente" else None)
            parte = forn or cli
            pdf = gerar_pdf(f"CONTRATO {c.numero}", [
                f"Contratante: {emp.razao_social}", f"Contratada/Contratante: {parte.nome}", "",
                f"Objeto: {c.objeto}", f"Vigência: {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}",
                f"Valor mensal: R$ {mensal:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "", "Cláusula 1ª - As partes acima identificadas têm entre si justo e contratado o presente instrumento.",
                "Cláusula 2ª - O reajuste anual seguirá o índice indicado neste contrato.",
                "Cláusula 3ª - Qualquer das partes poderá rescindir mediante aviso prévio de 30 dias.",
                "", "Documento de DEMONSTRAÇÃO gerado pelo Guará ERP."])
            doc = Documento.objects.create(pasta=pasta, titulo=f"Contrato {c.numero} — {titulo}"[:200], tipo="contrato",
                                           validade=fim, tags=f"contrato, {c.numero}")
            doc.adicionar_versao(ContentFile(pdf, name=f"{c.numero}.pdf"), "Minuta", usuarios["juridico"])
            c.documento = doc
            c.status = status
            c.save()
            Evento.objects.create(contrato=c, tipo="criacao", usuario=usuarios["juridico"], descricao="Contrato cadastrado.")
        rascunho = Contrato.objects.get(status="rascunho")
        rascunho.enviar_para_aprovacao(usuarios["juridico"])

        admin = User.objects.filter(is_superuser=True).first() or User.objects.get(username="diretor")
        proc = Processo.objects.get(codigo="aprovacao-pagamento")
        for cp in ContaPagar.objects.em_aberto().filter(vencimento__gte=hoje)[:3]:
            proc.iniciar(f"Aprovar pagamento: {cp}", admin, f"Fornecedor: {cp.fornecedor}", objeto=cp)

        self._compras(usuarios, forns)

        self.stdout.write(self.style.SUCCESS(
            "Dados de demonstração criados. Usuários: diretor, gerente, coordenador, financeiro, rh, fiscal, "
            "juridico, compras "
            f"(senha: {senha})."))

    def _compras(self, usuarios, forns):
        """Três pedidos em estágios diferentes: em aprovação, recebido e conferido, e recebido com divergência."""
        from apps.compras.models import AlcadaCompra, ItemSolicitacao, SolicitacaoCompra
        from apps.core.models import Departamento
        from apps.core.middleware import _local
        from apps.fiscal.models import ItemNota, NotaFiscal, Produto
        from apps.workflow.models import Instancia

        papel = Produto.objects.create(codigo="MAT001", descricao="Papel A4 (resma)", ncm="48025610", preco=0)
        toner = Produto.objects.create(codigo="MAT002", descricao="Toner para impressora", ncm="84439933", preco=0)
        papelaria, distribuidora = forns[0], forns[4]
        comprador = usuarios["compras"]
        AlcadaCompra.objects.create(coordenador=usuarios["coordenador"], limite_coordenador=1000,
                                    gerente=usuarios["gerente"], limite_gerente=10000, diretor=usuarios["diretor"])

        def aprovar(p):
            for inst in Instancia.objects.filter(object_id=p.pk, status="andamento"):
                while inst.status == "andamento":
                    inst.avancar(inst.responsavel_atual, "aprovar")
            p.refresh_from_db()

        def pedido(solicitante, itens, precos_por_fornecedor, vencedor):
            _local.usuario = solicitante
            sc = SolicitacaoCompra.objects.create(justificativa="Reposição do almoxarifado",
                                                  departamento=Departamento.objects.get(codigo="ADM"))
            for prod, qtd in itens:
                ItemSolicitacao.objects.create(solicitacao=sc, produto=prod, quantidade=qtd)
            _local.usuario = comprador
            cot = sc.gerar_cotacao(list(precos_por_fornecedor))
            for item in cot.itens.all():
                item.valor_unitario = precos_por_fornecedor[item.fornecedor][item.produto]
                item.prazo_entrega_dias = 5
                item.save()
            p = cot.escolher(vencedor, comprador)
            _local.usuario = None
            return p

        def nota(p, numero, precos):
            n = NotaFiscal.objects.create(tipo="entrada", modelo="55", fornecedor=p.fornecedor, numero=numero,
                                          status="emitida", natureza_operacao="Compra para uso e consumo")
            for item in p.itens.all():
                ItemNota.objects.create(nota=n, produto=item.produto, descricao=item.descricao,
                                        quantidade=item.quantidade, valor_unitario=precos[item.produto])
            n.recalcular()
            return n

        # 1) R$ 1.410: acima do coordenador. A coordenadora já aprovou; aguarda o gerente.
        p1 = pedido(usuarios["fiscal"], [(papel, 20), (toner, 3)],
                    {papelaria: {papel: 24, toner: 310}, distribuidora: {papel: 26, toner: 295}}, papelaria)
        inst = p1.enviar_para_aprovacao(comprador)
        inst.avancar(usuarios["coordenador"], "aprovar", "De acordo; segue para o gerente.")

        # 2) Aprovado pela coordenadora, recebido com nota conferida: conta a pagar liberada.
        p2 = pedido(usuarios["rh"], [(papel, 10)], {papelaria: {papel: 25}}, papelaria)
        p2.enviar_para_aprovacao(comprador)
        aprovar(p2)
        p2.receber({str(i.pk): i.quantidade for i in p2.itens.all()}, nota(p2, 4512, {papel: 25}),
                   usuario=comprador)

        # 3) Aprovado, mas a nota veio com o toner 10% mais caro: conta bloqueada e tarefa aberta.
        p3 = pedido(usuarios["financeiro"], [(toner, 2)], {distribuidora: {toner: 300}}, distribuidora)
        p3.enviar_para_aprovacao(comprador)
        aprovar(p3)
        p3.receber({str(i.pk): i.quantidade for i in p3.itens.all()}, nota(p3, 88231, {toner: 330}),
                   usuario=comprador)
