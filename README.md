# ERP Empresa

Sistema web de gestão empresarial (ERP) em **Python/Django + PostgreSQL**, roda em **Linux e Windows**
(com ou sem Docker) e é usado pelo navegador. Interface em português, pensada para empresas brasileiras
(CPF/CNPJ, PIX, NF-e, plano de contas).

## Módulos

| Módulo | O que faz |
|---|---|
| **Cadastros** | Empresas/filiais, departamentos (hierarquia), cargos, colaboradores, fornecedores, clientes. Validação de CPF/CNPJ e busca de CEP automática. |
| **Financeiro** | Contas a pagar e a receber com parcelamento, juros/desconto, baixa total ou parcial, estorno e cancelamento. Contas bancárias com saldo, extrato, transferências, plano de contas e centros de custo. Fluxo de caixa realizado × previsto com saldo projetado. |
| **Cobrança** | Cobranças por PIX (gera o **PIX copia-e-cola + QR Code** a partir da chave da empresa), boleto ou link. **Régua de cobrança** automática por e-mail (ex.: 3 dias antes, no dia, 3 e 10 dias após), com histórico de envios. Status sincronizado com o título. |
| **Fiscal** | Produtos/serviços com NCM, CFOP e alíquotas. Notas fiscais (NF-e, NFC-e, NFS-e) com itens e cálculo de ICMS/IPI/PIS/COFINS/ISS. **Importação de XML de NF-e** de fornecedores (cadastra o fornecedor e gera a conta a pagar). Geração de títulos a partir da nota e resumo para apuração. |
| **Workflow / BPM** | Modelagem de processos com etapas (tarefa ou aprovação), responsável por usuário ou grupo e prazo. Diagrama do fluxo, caixa "Minhas tarefas", histórico completo. Integração pronta: **aprovação de pagamento** de contas a pagar. |
| **GED** | Pastas com controle de acesso por grupo, documentos com **versões**, hash SHA-256, validade (contratos, alvarás), documentos confidenciais, download auditado e visualização no navegador. |
| **Administração** | Usuários, grupos e permissões; **auditoria** automática de toda inclusão/alteração/exclusão (com o que mudou); página de **Banco de dados** (tamanho, tabelas, backup `.sql` com um clique, VACUUM) e **pgAdmin** incluso no Docker. |

Toda tela de listagem tem busca, filtros, ordenação, paginação e **exportação para CSV/Excel**.
Painel inicial com saldos, vencidos, próximos pagamentos/recebimentos, tarefas e gráfico de fluxo de caixa.
Tema claro/escuro. Bibliotecas de interface ficam no próprio projeto (funciona em rede sem internet).

## Instalação rápida com Docker (Linux ou Windows)

Requer Docker (no Windows: Docker Desktop).

```bash
git clone <url-do-repositorio> erp-empresa
cd erp-empresa
cp .env.example .env        # no Windows: copy .env.example .env
# edite o .env: senhas (POSTGRES_PASSWORD, POSTGRES_ADMIN_PASSWORD, PGADMIN_PASSWORD...) e a chave secreta:
#   python -c "import secrets; print(secrets.token_urlsafe(50))"
docker compose up -d --build
# opcional, HTTPS com o proxy Caddy:  docker compose --profile https up -d
```

| Serviço | Endereço |
|---|---|
| ERP | http://localhost:8000 (usuário/senha do `DJANGO_SUPERUSER_*` do `.env`) |
| pgAdmin | http://localhost:5050, **só no próprio servidor** (`PGADMIN_EMAIL` / `PGADMIN_PASSWORD`; conecte com o usuário `postgres` e a `POSTGRES_ADMIN_PASSWORD`) |

O serviço `agendador` executa a régua de cobrança todo dia às 08:00.

## Instalação sem Docker

### Linux (Debian/Ubuntu)

```bash
sudo ./scripts/instalar_linux.sh
```

Instala PostgreSQL e Python, cria banco e `.env` (com senha gerada), aplica a estrutura e registra os
serviços `erp-web` (gunicorn, porta 8000) e `erp-agendador` no systemd.

### Windows

1. Instale o [Python 3.11+](https://www.python.org/downloads/) (marque "Add to PATH") e o
   [PostgreSQL](https://www.postgresql.org/download/windows/).
2. No PowerShell, dentro da pasta do projeto:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\scripts\instalar_windows.ps1      # 1ª vez cria o .env e abre no Bloco de Notas; ajuste e rode de novo
   .\scripts\iniciar_windows.bat       # inicia em http://localhost:8000
   ```
3. Para a régua de cobrança diária, agende `scripts\regua_windows.bat` no Agendador de Tarefas.

> Para acessar de outros computadores da rede, inclua o IP/nome do servidor em `DJANGO_ALLOWED_HOSTS`
> e `DJANGO_CSRF_TRUSTED_ORIGINS` no `.env`.

## Primeiros passos

1. Entre com o administrador e cadastre sua **empresa** (Cadastros → Empresas), incluindo a **chave PIX**.
2. Cadastre **contas bancárias** com o saldo inicial (Financeiro → Contas bancárias).
3. Crie os **usuários** (Administração → Usuários e grupos) e coloque cada um em um grupo:
   `Administradores`, `Financeiro`, `Fiscal`, `RH`, `Compras` ou `Usuários`. As permissões de cada grupo
   são criadas pelo comando `setup_erp` e podem ser ajustadas no Admin.
4. Ajuste o plano de contas, a régua de cobrança e os processos de workflow conforme sua empresa.

Dados de demonstração (empresa fictícia, colaboradores, títulos, notas, processos e usuários
`financeiro`, `rh`, `fiscal`, `diretor` com senha `demo1234`). **Só para testes, nunca em produção:**

```bash
python manage.py seed_demo          # Docker: docker compose exec web python manage.py seed_demo
```

## Comandos úteis

| Comando | Para quê |
|---|---|
| `python manage.py setup_erp` | Cria/atualiza grupos, permissões, plano de contas, régua e processos padrão (pode repetir). |
| `python manage.py seed_demo` | Carrega dados fictícios (só em banco vazio). |
| `python manage.py executar_regua` | Envia agora as cobranças previstas pela régua. |
| `python manage.py agendador --hora 08:00` | Fica rodando e executa a régua todo dia no horário. |
| `python manage.py test tests` | Roda a suíte de testes automatizados. |
| `docker compose exec db pg_dump -U postgres erp > backup.sql` | Backup pelo terminal. |

## Segurança

Resumo do que já vem configurado (detalhes, portas e checklist em **[SEGURANCA.md](SEGURANCA.md)**):

- O ERP conecta ao PostgreSQL com um usuário **sem superusuário**. O banco não fica exposto fora do Docker
  e o pgAdmin só escuta no próprio servidor.
- Bloqueio de login após tentativas erradas, auditoria de logins com IP e sessões que expiram.
- Permissões por grupo e por registro: solicitações de workflow só para os envolvidos, pastas do GED por grupo.
  Dados de colaboradores ficam restritos ao RH.
- Proteção contra injeção de SQL e de template, XSS, XXE, upload de executáveis e fórmulas em CSV.
  Tudo coberto por testes automatizados.
- A página **Administração → Banco de dados** mostra uma verificação de segurança da instalação.

## Desenvolvimento

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # POSTGRES_HOST=localhost e DJANGO_DEBUG=1
# os testes criam um banco temporário: no ambiente de DESENVOLVIMENTO dê CREATEDB ao usuário
#   ALTER ROLE erp CREATEDB;   (em produção, não)
python manage.py migrate && python manage.py seed_demo && python manage.py createsuperuser
python manage.py runserver
```

### Estrutura

```
config/            configurações Django (settings lidos do .env)
apps/core/         cadastros, auditoria, painel, banco de dados e o motor de telas (crud.py)
apps/financeiro/   títulos, baixas, contas bancárias, extrato, fluxo de caixa
apps/cobranca/     cobranças, PIX (pix.py), régua e histórico
apps/fiscal/       produtos, notas fiscais, importador de XML NF-e
apps/workflow/     processos BPM, etapas, solicitações e histórico
apps/ged/          pastas, documentos e versões
templates/         telas (Bootstrap 5)
docker/            entrypoint, criação do usuário restrito do banco, pgAdmin e proxy HTTPS (Caddy)
scripts/           instaladores Linux/Windows
tests/             testes automatizados
```

**Como adicionar uma tela nova:** crie o modelo, depois uma classe em `telas.py` do módulo:

```python
class VeiculoCrud(Crud):
    model = Veiculo
    list_display = ("placa", "modelo", "responsavel")
    search_fields = ("placa", "modelo")
    filter_fields = ("modelo",)
    menu = "Cadastros"
    icon = "car-front"
```

e inclua `VeiculoCrud` em `crud_urls(...)` no `urls.py` do módulo. Listagem, filtros, exportação,
formulário, detalhe, exclusão, menu e permissões são gerados automaticamente.

## Limites desta versão (MVP)

- **Emissão de NF-e/NFS-e**: o sistema calcula, numera e registra as notas, mas **não transmite à SEFAZ
  ou à prefeitura**. Use seu emissor/certificado A1 e informe chave e protocolo, ou integre um serviço
  (ex.: Focus NFe, PlugNotas, NFE.io) em `apps/fiscal`.
- **Boleto registrado / PIX dinâmico**: o PIX gerado é o copia-e-cola **estático** (sem confirmação automática).
  Boleto e PIX com baixa automática exigem integração com o banco ou um gateway (Asaas, Iugu, Gerencianet etc.).
- **Folha de pagamento** e **estoque** não fazem parte desta versão.
- Os cálculos de impostos são gerenciais; a apuração oficial deve ser validada pela contabilidade.
