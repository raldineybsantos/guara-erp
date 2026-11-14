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
                ("gerente", "Usuários", "Gustavo Gerente", "23456789173", "ADM"),
                ("vendedor", "Vendas", "Vera Vendas", "34567890094", "ADM"),
                ("almoxarife", "Estoque", "Alan Almoxarife", "34567890175", "ADM"),
                ("producao", "Produção", "Paulo Produção", "34567890256", "ADM"),
                ("sesmt", "SESMT", "Sara Segurança", "34567890337", "RH"),
                ("analista", "Service Desk", "Igor Infra", "45678901087", "ADM"),
                ("contador", "Contabilidade", "Caio Contador", "56789012060", "FIN")):
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
        self._operacao(usuarios, clis)
        self._servicedesk(usuarios)
        # Contabilidade: os lançamentos automáticos já nasceram com os dados acima; falta só o capital inicial.
        from apps.contabilidade import servicos
        from apps.contabilidade.models import ContaContabil

        servicos.gravar(hoje.replace(month=1, day=1), "Integralização do capital social",
                        ContaContabil.objects.get(codigo="1.1.1"), ContaContabil.objects.get(codigo="2.3.1"), 150000,
                        manual=True)

        self.stdout.write(self.style.SUCCESS(
            "Dados de demonstração criados. Usuários: diretor, gerente, coordenador, financeiro, rh, fiscal, "
            "juridico, compras, vendedor, almoxarife, producao, sesmt, analista, contador "
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

    def _operacao(self, usuarios, clis):
        """Estoque, vendas, produção, qualidade, manutenção, projetos, CRM e SST com um exemplo de cada."""
        from apps.core.middleware import _local
        from apps.core.models import Colaborador, Departamento
        from apps.crm.models import Interacao, Oportunidade
        from apps.estoque import models as est
        from apps.fiscal.models import Produto
        from apps.manutencao.models import Equipamento, OrdemServico, PlanoManutencao
        from apps.producao.models import ComponenteEstrutura, EstruturaProduto, OrdemProducao
        from apps.projetos.models import ApontamentoHoras, Projeto, TarefaProjeto
        from apps.qualidade.models import NaoConformidade
        from apps.sst.models import AvaliacaoRisco, EntregaEPI, ExameOcupacional, Incidente, TreinamentoSST
        from apps.vendas.models import ItemPedidoVenda, PedidoVenda

        hoje = datetime.date.today()
        almox = est.Armazem.principal()

        def prod(codigo, desc, preco=0, minimo=0, **kw):
            return Produto.objects.create(codigo=codigo, descricao=desc, preco=Decimal(preco), ncm="94036000",
                                          estoque_minimo=minimo, aliquota_icms=Decimal("18"), **kw)

        # Estoque inicial e uma mesa fabricada a partir de tampo e pés.
        mesa = prod("PA001", "Mesa de escritório 1,20 m", 890, 5)
        tampo = prod("MP001", "Tampo MDF 1,20 m", 0, 10)
        pe = prod("MP002", "Pé metálico", 0, 40)
        luva = prod("EPI01", "Luva anticorte", 0, 20)
        _local.usuario = usuarios["almoxarife"]
        est.entrada(tampo, 12, "180.00", almox, "ajuste", "SALDO-INI", "Saldo inicial")
        est.entrada(pe, 30, "22.50", almox, "ajuste", "SALDO-INI", "Saldo inicial")
        est.entrada(luva, 15, "18.90", almox, "ajuste", "SALDO-INI", "Saldo inicial")
        e = EstruturaProduto.objects.create(produto=mesa, observacoes="Montar os pés no tampo; conferir esquadro.")
        ComponenteEstrutura.objects.create(estrutura=e, componente=tampo, quantidade=1)
        ComponenteEstrutura.objects.create(estrutura=e, componente=pe, quantidade=4, perda_pct=Decimal("2.5"))

        _local.usuario = usuarios["producao"]
        op1 = OrdemProducao.objects.create(produto=mesa, quantidade=6, data_prevista=hoje + datetime.timedelta(days=5))
        op1.liberar()
        op1.apontar(6, usuarios["producao"], "Lote 1")
        op2 = OrdemProducao.objects.create(produto=mesa, quantidade=10, data_prevista=hoje + datetime.timedelta(days=15))
        op2.liberar()  # faltam pés e tampos: use "Comprar o que falta"

        # Venda faturada (baixa 2 mesas) e outra aguardando confirmação.
        _local.usuario = usuarios["vendedor"]
        pv = PedidoVenda.objects.create(cliente=clis[1], condicao_pagamento="30/60")
        ItemPedidoVenda.objects.create(pedido=pv, produto=mesa, quantidade=2)
        pv.recalcular()
        pv.confirmar()
        pv.faturar()
        pv2 = PedidoVenda.objects.create(cliente=clis[3], condicao_pagamento="28")
        ItemPedidoVenda.objects.create(pedido=pv2, produto=mesa, quantidade=3, desconto_pct=5)
        pv2.recalcular()

        # Funil de vendas.
        for titulo, conta, estagio, valor, dias in (
                ("Mobiliário da nova filial", "Construtora Pilar", "negociacao", 48000, 20),
                ("Escritório completo", "Coworking Centro", "proposta", 26500, 35),
                ("Reposição anual", "Escola Saber", "qualificacao", 9000, 60),
                ("Linha gamer", "Loja Pixel", "prospeccao", 15000, 90)):
            cli = next((c for c in clis if c.nome == conta), None)
            op = Oportunidade.objects.create(titulo=titulo, cliente=cli, prospect_empresa="" if cli else conta,
                                             estagio=estagio, valor=valor, origem="indicacao" if cli else "site",
                                             previsao_fechamento=hoje + datetime.timedelta(days=dias),
                                             responsavel=usuarios["vendedor"])
            if estagio == "negociacao":
                Interacao.objects.create(oportunidade=op, tipo="reuniao", resumo="Apresentação da proposta.",
                                         proximo_passo="Enviar proposta com prazo de 45 dias",
                                         data_proximo_passo=hoje + datetime.timedelta(days=2))

        # Qualidade: reclamação de cliente.
        NaoConformidade.objects.create(titulo="Mesa entregue com risco no tampo", origem="cliente", cliente=clis[1],
                                       produto=mesa, gravidade="media", responsavel=usuarios["producao"],
                                       descricao="Cliente relatou risco de 10 cm no tampo de uma das mesas.",
                                       acao_imediata="Troca do tampo agendada.")

        # Manutenção.
        _local.usuario = usuarios["producao"]
        dep = Departamento.objects.get(codigo="ADM")
        comp = Equipamento.objects.create(codigo="CMP-01", nome="Compressor de ar 20 pés", tipo="Compressor",
                                          fabricante="Schulz", departamento=dep, localizacao="Galpão", critico=True)
        Equipamento.objects.create(codigo="AR-03", nome="Ar-condicionado sala de reuniões", tipo="Climatização",
                                   departamento=dep, localizacao="Sala 3")
        PlanoManutencao.objects.create(equipamento=comp, descricao="Troca de óleo e filtro", periodicidade_dias=90,
                                       proxima=hoje + datetime.timedelta(days=4), checklist="Drenar\nTrocar filtro")
        from apps.manutencao.models import gerar_preventivas
        gerar_preventivas()
        OrdemServico.objects.create(equipamento=comp, tipo="corretiva", prioridade="alta",
                                    descricao="Vazamento de ar na conexão de saída.")

        # Projeto com EAP e horas.
        _local.usuario = usuarios["diretor"]
        prj = Projeto.objects.create(nome="Implantação do Guará ERP", gerente=usuarios["diretor"], orcamento=60000,
                                     status="andamento", departamento=dep, fim_previsto=hoje + datetime.timedelta(days=90))
        t = None
        for eap, nome, horas, pct, resp in (("1", "Levantamento de processos", 40, 100, "financeiro"),
                                            ("2", "Cadastros e migração", 80, 60, "fiscal"),
                                            ("3", "Treinamento", 24, 0, "rh")):
            t = TarefaProjeto.objects.create(projeto=prj, eap=eap, nome=nome, horas_previstas=horas, percentual=pct,
                                             responsavel=usuarios[resp])
            if pct:
                _local.usuario = usuarios[resp]
                ApontamentoHoras.objects.create(projeto=prj, tarefa=t, horas=Decimal(horas * pct / 100 / 4),
                                                descricao=nome, data=hoje - datetime.timedelta(days=3))
        # SST.
        _local.usuario = usuarios["sesmt"]
        colab = Colaborador.objects.get(usuario=usuarios["producao"])
        AvaliacaoRisco.objects.create(departamento=dep, atividade="Montagem de mesas", perigo="Prensamento de mãos",
                                      tipo="acidente", probabilidade=3, severidade=4,
                                      medidas_controle="Luva anticorte; procedimento de montagem; treinamento.")
        AvaliacaoRisco.objects.create(departamento=dep, atividade="Compressor", perigo="Ruído acima de 85 dB",
                                      tipo="fisico", probabilidade=4, severidade=3, medidas_controle="Protetor auricular")
        EntregaEPI.objects.create(colaborador=colab, descricao="Luva anticorte", ca="38927", produto=luva, quantidade=2,
                                  validade=hoje + datetime.timedelta(days=60), recebido_confirmado=True)
        ExameOcupacional.objects.create(colaborador=colab, tipo="periodico", resultado="apto",
                                        data=hoje - datetime.timedelta(days=345),
                                        validade=hoje + datetime.timedelta(days=20), medico="Dra. Lima CRM-SP 12345")
        TreinamentoSST.objects.create(colaborador=colab, norma="NR-12", descricao="Segurança em máquinas",
                                      carga_horaria=8, validade=hoje + datetime.timedelta(days=300))
        _local.usuario = usuarios["producao"]
        Incidente.objects.create(tipo="quase", gravidade="leve", departamento=dep, local="Galpão, bancada 2",
                                 descricao="Pé metálico caiu da prateleira; ninguém ferido.")
        _local.usuario = None
        from apps.sst.models import alertar_vencimentos
        alertar_vencimentos()

    def _servicedesk(self, usuarios):
        """Áreas, catálogo, biblioteca, chamados em vários estágios e duas mudanças."""
        from apps.core.middleware import _local
        from apps.servicedesk.models import Area, ArtigoConhecimento, Chamado, Mudanca, Servico

        u = usuarios
        for nome in ("Usuários",):
            g = Group.objects.get(name=nome)
            for k in ("analista", "vendedor", "almoxarife", "producao", "sesmt", "compras"):
                u[k].groups.add(g)
        u["producao"].groups.add(Group.objects.get(name="Manutenção"))  # Paulo também cuida da manutenção
        cab = Group.objects.get(name="CAB")
        cab.user_set.add(u["diretor"], u["gerente"])
        ti = Area.objects.create(nome="TI", grupo=Group.objects.get(name="Service Desk"), gestor=u["gerente"],
                                 icone="laptop")
        fac = Area.objects.create(nome="Facilities", grupo=Group.objects.get(name="Manutenção"), gestor=u["diretor"],
                                  icone="building")
        rh = Area.objects.create(nome="RH", grupo=Group.objects.get(name="RH"), icone="people")
        fin = Area.objects.create(nome="Financeiro", grupo=Group.objects.get(name="Financeiro"), icone="cash-coin")
        S = {}
        for area, nome, chaves, resp, sol, aprov, desc in (
                (ti, "Senha e acesso", "senha bloqueada login acesso conta", 1, 4, "nenhum",
                 "Desbloqueio, reset de senha e acesso a sistemas."),
                (ti, "Problema no computador", "computador notebook lento travando tela impressora", 2, 8, "nenhum",
                 "Equipamento com defeito, lentidão, impressora."),
                (ti, "Novo equipamento", "notebook computador monitor mouse teclado novo", 4, 40, "gestor_solicitante",
                 "Solicitação de notebook, monitor e periféricos. Precisa da aprovação do seu gestor."),
                (ti, "VPN e acesso remoto", "vpn remoto home office conectar", 1, 8, "nenhum", "Configuração e falhas."),
                (fac, "Manutenção predial", "ar condicionado lâmpada fechadura vazamento cadeira mesa", 2, 24, "nenhum",
                 "Ar-condicionado, iluminação, fechaduras, mobiliário."),
                (fac, "Reserva de sala e eventos", "sala reunião evento coffee", 4, 16, "nenhum", "Salas e apoio a eventos."),
                (rh, "Dúvida sobre holerite e benefícios", "holerite salário vale benefício férias ponto", 4, 24,
                 "nenhum", "Holerite, benefícios, férias e ponto."),
                (fin, "Reembolso de despesas", "reembolso despesa viagem nota", 4, 40, "gestor_solicitante",
                 "Reembolso de despesas de viagem e compras com aprovação do gestor.")):
            S[nome] = Servico.objects.create(area=area, nome=nome, palavras_chave=chaves, sla_resposta_horas=resp,
                                             sla_solucao_horas=sol, aprovacao=aprov, descricao=desc)
        for titulo, area, servico, sintoma, solucao, chaves, publico in (
                ("Desbloquear a senha da rede", ti, "Senha e acesso", "Conta bloqueada depois de errar a senha.",
                 "1. Aguarde 15 minutos: o bloqueio sai sozinho.\n2. Se precisar antes, use o autoatendimento em "
                 "senha.empresa.local com seu CPF.\n3. Ainda bloqueada? Abra um chamado em Senha e acesso.",
                 "senha bloqueada bloqueio conta", True),
                ("VPN não conecta em casa", ti, "VPN e acesso remoto", "A VPN fica em 'conectando' ou dá erro de certificado.",
                 "1. Confira data e hora do computador.\n2. Reinicie o cliente da VPN.\n3. Teste em outra rede (celular).\n"
                 "4. Persistindo, abra chamado com o print do erro.", "vpn remoto conectar certificado", True),
                ("Impressora não imprime", ti, "Problema no computador", "Documento fica na fila e não sai.",
                 "1. Cancele a fila.\n2. Desligue e ligue a impressora.\n3. Imprima de novo escolhendo 'Impressora-Andar2'.",
                 "impressora fila imprimir", True),
                ("Ar-condicionado pingando", fac, "Manutenção predial", "Água pingando do aparelho.",
                 "Desligue o aparelho e abra chamado em Manutenção predial informando a sala.",
                 "ar condicionado pingando água", True),
                ("Reset de MFA no AD (interno)", ti, "Senha e acesso", "Usuário trocou de celular e perdeu o MFA.",
                 "Validar identidade por vídeo; no console de identidade, remover os métodos MFA e pedir novo cadastro.",
                 "mfa autenticador celular", False)):
            ArtigoConhecimento.objects.create(titulo=titulo, area=area, servico=S[servico], sintoma=sintoma,
                                              solucao=solucao, palavras_chave=chaves, publico=publico)

        def abrir(quem, servico, titulo, descricao, prioridade=""):
            _local.usuario = u[quem]
            try:
                return Chamado.objects.create(servico=S[servico], titulo=titulo, descricao=descricao,
                                              prioridade=prioridade)
            finally:
                _local.usuario = None

        abrir("coordenador", "Problema no computador", "Notebook muito lento",
              "Desde a atualização de ontem o notebook demora 10 minutos para ligar.", "alta")
        c = abrir("fiscal", "VPN e acesso remoto", "VPN não conecta em home office", "Erro 809 ao conectar.")
        c.assumir(u["analista"])
        c.chamar_usuario(u["analista"], "Pode me mandar um print do erro e dizer qual internet está usando?")
        c.enviar_mensagem(u["fiscal"], "Estou na internet de casa (fibra). O erro é 809.")
        c.enviar_mensagem(u["analista"], "Provável bloqueio da porta no roteador. Vou te ligar às 14h.")
        abrir("rh", "Novo equipamento", "Monitor adicional", "Preciso de um segundo monitor para conferir planilhas.")
        c = abrir("compras", "Senha e acesso", "Senha do e-mail bloqueada", "Errei a senha.")
        c.assumir(u["analista"])
        c.resolver(u["analista"], "Desbloqueada; orientado a usar o autoatendimento.",
                   ArtigoConhecimento.objects.get(titulo="Desbloquear a senha da rede"))
        c = abrir("vendedor", "Manutenção predial", "Ar-condicionado pingando na sala 3", "Pingando sobre a mesa.")
        c.assumir(u["producao"])
        c.resolver(u["producao"], "Dreno desentupido.")
        c.fechar(u["vendedor"], 5, "Resolveram no mesmo dia!")
        c = abrir("almoxarife", "Problema no computador", "Leitor de código de barras não lê",
                  "O leitor do almoxarifado parou de ler etiquetas.", "critica")
        Chamado.objects.filter(pk=c.pk).update(prazo_solucao=timezone.now() - datetime.timedelta(hours=1),
                                               sla_iniciado_em=timezone.now() - datetime.timedelta(hours=5))

        agora = timezone.now()
        base = dict(area=ti, responsavel=u["analista"], impacto="Internet indisponível por até 15 minutos.",
                    justificativa="Correção de vulnerabilidade crítica divulgada pelo fabricante.",
                    plano_implantacao="1. Backup da configuração\n2. Aplicar firmware\n3. Reiniciar",
                    plano_testes="Navegação, VPN e e-mail a partir de 3 estações.",
                    plano_retorno="Restaurar o firmware e a configuração do backup.")
        _local.usuario = u["analista"]
        m = Mudanca.objects.create(titulo="Atualizar firmware do firewall", tipo="normal", risco="medio",
                                   descricao="Firmware 7.2.8 → 7.2.10",
                                   janela_inicio=(agora + datetime.timedelta(days=3)).replace(hour=22, minute=0),
                                   janela_fim=(agora + datetime.timedelta(days=3)).replace(hour=23, minute=0), **base)
        m.chamados.add(Chamado.objects.get(titulo="VPN não conecta em home office"))
        inst = m.enviar_para_aprovacao(u["analista"])
        inst.avancar(u["gerente"], "aprovar", "Ok, fora do horário comercial.")
        Mudanca.objects.create(titulo="Trocar switch do 2º andar", tipo="normal", risco="alto",
                               descricao="Substituir switch com portas queimadas.",
                               janela_inicio=(agora + datetime.timedelta(days=7)).replace(hour=19, minute=0),
                               janela_fim=(agora + datetime.timedelta(days=7)).replace(hour=21, minute=0), **base)
        _local.usuario = None

