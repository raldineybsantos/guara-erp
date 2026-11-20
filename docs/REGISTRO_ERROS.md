# Guará ERP — Registro de erros e ajustes encontrados nos testes

Atualizado em 20/11/2026 (2ª rodada). Origem: rodada de testes pela interface web (empresa, departamentos, usuários, compras,
vendas, contabilidade) e implantação no servidor vsl01.

## Corrigidos

| # | Onde | Problema encontrado | Correção | Commit |
|---|------|---------------------|----------|--------|
| 1 | Cadastros / menu | "Produtos e serviços" não tinha entrada no menu; o usuário não achava o cadastro | Item de menu em Cadastros (permissão `fiscal.view_produto`) | 46c144d |
| 2 | Compras e Vendas | Botões de ação apareciam em situações inválidas (ex.: Faturar em rascunho, Receber em pedido não aprovado) | Novo atributo `acao_status` no motor de CRUD: cada botão só aparece nas situações permitidas | b66644b |
| 3 | Mensagens (todos os módulos) | Valores apareciam sem o padrão brasileiro (ex.: 1234.5) | Função `moeda()` em `apps/core/formatacao.py` aplicada em compras, vendas, bancário, contabilidade, jurídico, RH e workflow | b66644b |
| 4 | Pedido de venda | Item não sugeria preço ao escolher o produto (o preço só entrava ao salvar) | Endpoint `vendas:preco_item` + `preco_item.js` preenchem o valor pela tabela do cliente ou pelo produto | b66644b |
| 5 | Primeiro acesso | Sem orientação do que configurar primeiro; o usuário novo ficava perdido | Tela "Primeiros passos" com checklist e aviso no painel | b66644b |
| 6 | Parceiros | Sem consulta de CNPJ nem validação de CPF no cadastro | Botão "Consultar CNPJ" (BrasilAPI) preenche razão social, endereço, CNAE e situação; CPF/CNPJ validados | 34b8765 |
| 7 | Contabilidade | Contratos não geravam apropriação mensal (despesa/receita) | Modelos `ContratoContabil` e `ApropriacaoContrato`, tela de apropriação e estorno por competência | 34b8765 |
| 8 | Colaboradores | Matrícula ficava vazia e precisava ser digitada à mão | Gerada automaticamente (6 dígitos, sequencial por empresa) quando em branco; matrícula manual é respeitada | a393e6c |
| 9 | Colaboradores | CPF sem máscara ao digitar e gravado ora com pontos, ora só com dígitos | Máscara visual 000.000.000-00; gravação sempre só com dígitos; lista continua mostrando formatado | a393e6c |
| 10 | Endereço (todos os cadastros) | CEP não preenchia o endereço | Ao sair do campo CEP, logradouro, bairro, cidade e UF são preenchidos (BrasilAPI) | a393e6c |
| 11 | Todos os módulos com situação (92 ações) | Botões de ação apareciam em qualquer situação do registro (ex.: Baixar em título pago, Estornar em título aberto, Aprovar férias já aprovadas, Liberar OP já encerrada) | `acao_status` aplicado a 32 telas: financeiro, cobrança, fiscal, jurídico, bancário, RH, produção, manutenção, qualidade, estoque, patrimônio e service desk; teste automático confere ações e situações | ff1f9c8 |
| 12 | Colaboradores e dependentes | Cadastro aceitava colaborador sem RG, e-mail, telefone, endereço, cargo, departamento ou salário; datas incoerentes e telefone/CEP inválidos passavam; dependente com dedução de IRRF sem CPF | Validações no formulário (obrigatórios, idade, datas, telefone, CEP, conta bancária completa ou PIX) e no dependente (CPF para IRRF, idade do cônjuge e do filho) | c2795fa |
| 13 | Documentos | Sem como anexar RG, comprovante de residência, certidões e carteira de vacinação, nem saber quem está com documento faltando | Documentos pessoais no GED (pasta restrita, antivírus, versões), catálogo configurável de documentos obrigatórios, checklist no colaborador e tela de pendências | c2795fa |
| 14 | Colaborador (tela) | Campo Gestor listava o próprio colaborador e aceitava ciclo na hierarquia | Lista sem a própria pessoa; ciclo de gestores é recusado | e9fff24 |
| 15 | Documento pessoal (tela) | Lista de dependentes mostrava os de todos os colaboradores | Filtra pelo colaborador escolhido; rótulo traz nome e parentesco | e9fff24 |
| 16 | Endereço por CEP | Mensagens de erro antigas continuavam na tela depois do preenchimento automático | Erros dos campos preenchidos somem ao buscar o CEP | e9fff24 |
| 17 | Detalhe de cadastros | CPF, CNPJ e telefone apareciam só com dígitos | Exibição formatada (000.000.000-00, (11) 98765-4321) na tela e nas exportações | e9fff24 |

| 18 | Uniformes (novo) | Sem como organizar uniformes: numeração, peças do conjunto, imagem e disponibilidade em estoque | Módulo Uniformes: catálogo com foto e grade de tamanhos, conjuntos com cobertura x necessidade, entrega com baixa de estoque, termo e estorno | (este commit) |

Nota: a suspeita de que o preço do produto não era aplicado ao pedido foi alarme falso; o modelo já usava a tabela ou
o preço do produto ao salvar com valor zero. O item 4 melhora apenas a visibilidade na tela.

## Implantação e ambiente

| # | Situação | Causa | Solução |
|---|----------|-------|---------|
| A | `git pull` no servidor: "not a git repository" | Comando executado em `/home/rbsantos`, fora da pasta do projeto | Entrar em `/home/rbsantos/guara-erp` antes de atualizar |
| B | `apt git install` / "Invalid operation git" | Comando digitado incorretamente | `apt install git` |
| C | Testes pedindo confirmação ao recriar o banco | Banco de teste remanescente | `python manage.py test --noinput` |
| D | Testes sem conexão | PostgreSQL parado | `service postgresql start` |
| E | Testes em paralelo falhavam (erro de pickle) | Limitação do modo paralelo | Executar em série |

Servidor vsl01 atualizado em 20/11/2026: containers web, agendador, banco, pgAdmin e ClamAV em execução.

## Em aberto (próximas correções)

1. Saldo inicial da conta bancária não gera lançamento contábil (o Caixa aparece a crédito após o primeiro pagamento).
2. Formulário de colaborador com cerca de 30 campos numa página só; sugestão: dividir em abas.
3. Diagrama do workflow mostra as três etapas de aprovação mesmo quando só a primeira é necessária.
4. Documento pessoal aceita qualquer conteúdo com extensão permitida (o teste usou um RTF com nome .pdf); conferir o tipo real do arquivo (PDF/JPG/PNG) ainda não foi feito.
5. Não testados: recebimento com vinculação do XML da NF-e (conferência de 3 vias) e liberação de pagamento.
6. Treze gerentes ainda sem vínculo com colaborador (faltam CPFs) e departamentos sem responsável.
7. Transmissão de NFS-e/NF-e e teste com certificado e-CNPJ A1 (deixados para o final).
