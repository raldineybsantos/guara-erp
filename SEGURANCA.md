# Segurança do ERP Empresa

Auditoria feita em 12/11/2026 sobre controle de acesso, acesso ao banco, manutenção, execução de
código malicioso via web e portas. Cada correção tem teste automatizado em `tests/test_seguranca.py`.

## 1. O banco executa código malicioso enviado pela web?

**Não.** Mas o projeto tinha duas falhas, já corrigidas:

| Vetor | Situação |
|---|---|
| **Injeção de SQL** (busca, filtros, ordenação, exportação) | Protegido. Todo acesso ao banco é feito pelo ORM do Django, com parâmetros. A ordenação só aceita nomes de campos conhecidos. Os únicos SQL escritos à mão (página Banco de dados) são fixos, não recebem dados do usuário e só superusuários os acessam. Testado com payloads `'; DROP TABLE`, `UNION SELECT`, `OR 1=1`. |
| **Injeção de template na régua de cobrança** | **CORRIGIDO (grave).** O texto do e-mail era processado pelo motor de templates do Django com objetos do banco no contexto. Quem tinha acesso à régua conseguia ler o hash de senha dos usuários com `{{ cobranca.criado_por.password }}`. Agora existe só a troca de variáveis de texto (lista fechada). Tags `{% %}` e variáveis fora da lista são recusadas, e os modelos antigos foram migrados. |
| **XML malicioso (XXE / "bomba de entidades")** na importação de NF-e | **CORRIGIDO.** O parser agora é o `defusedxml`, que recusa DTD, entidades externas e expansão exponencial. Só arquivos `.xml` são aceitos. |
| **Execução de comandos no servidor** | O backup chama `pg_dump` com a lista de argumentos fixa (sem shell), e só para superusuários. Nenhuma entrada do usuário chega a comandos. |
| **Upload de arquivo malicioso** (GED, XML da nota) | **CORRIGIDO.** Executáveis, scripts, `.html` e `.svg` são bloqueados, com limite de tamanho (`ARQUIVO_MAX_MB`). Os arquivos nunca são servidos diretamente pela URL `/media/`, só por views que conferem permissão. A visualização no navegador vale só para PDF, imagens e TXT, com `Content-Security-Policy: sandbox` e `nosniff`. O resto é sempre baixado. |
| **XSS** (scripts em campos de texto) | Protegido pelo escape automático dos templates. Testado com `<script>` em nome, descrição e categoria. |
| **Fórmulas na exportação CSV** (`=HYPERLINK(...)` executado no Excel) | **CORRIGIDO.** Células que começam com `= + - @` recebem um apóstrofo (números negativos são preservados). |

### Defesa em profundidade no PostgreSQL

**CORRIGIDO (grave no Docker):** a imagem oficial do PostgreSQL transforma `POSTGRES_USER` em
**superusuário**. Com isso, o ERP conectava como superusuário: qualquer falha futura de injeção de SQL
permitiria executar comandos no servidor (`COPY ... TO PROGRAM`), ler arquivos (`pg_read_file`) ou
instalar extensões.

Agora:

- O superusuário `postgres` (senha `POSTGRES_ADMIN_PASSWORD`) é usado só pelo pgAdmin e para manutenção.
- O ERP usa o usuário `erp`, criado por `docker/initdb/01-erp.sh` com `NOSUPERUSER NOCREATEDB NOCREATEROLE`.
  Ele é dono apenas do banco `erp`, e `PUBLIC` não pode conectar nem criar objetos.
- Os instaladores de Linux e Windows criam o usuário do mesmo jeito.
- Validado: com esse usuário, `COPY TO PROGRAM`, `pg_read_file` e `CREATE EXTENSION` são negados.
  Migrações, backup (`pg_dump`) e `VACUUM ANALYZE` pela tela continuam funcionando.
- A página **Administração → Banco de dados** mostra uma verificação de segurança: alerta se o ERP
  estiver conectado como superusuário, se `DEBUG` estiver ligado, se o acesso for HTTP e quantas falhas
  de login houve em 24h.

> Instalação Docker já existente: o script de init só roda na criação do volume. Para migrar, rode no pgAdmin
> `ALTER ROLE erp NOSUPERUSER NOCREATEDB NOCREATEROLE;` conectado como outro superusuário
> (crie um antes, se `erp` for o único).

## 2. Gerenciamento de acesso

| Item | Situação |
|---|---|
| Login | Bloqueio após 5 senhas erradas em 15 min por usuário (e 15 por IP), configurável. Responde HTTP 429. O login do `/admin/` passa pela mesma tela. |
| Auditoria de login | Login, logout e falhas registrados com IP. |
| Senhas | Hash PBKDF2 (padrão Django), mínimo de 10 caracteres, bloqueio de senhas comuns ou numéricas. |
| Sessão | Expira em 8h (`SESSAO_HORAS`) e ao fechar o navegador. Cookies `HttpOnly` e `SameSite=Lax`, e `Secure` com `DJANGO_HTTPS=1`. |
| Chave secreta | O sistema **não inicia** em produção sem `DJANGO_SECRET_KEY` forte (≥ 32 caracteres). Antes, ele usava uma chave conhecida, o que permitia forjar sessões. |
| Ações escondidas na tela | **CORRIGIDO.** Telas sem "Novo"/"Editar" (solicitações de workflow, auditoria, históricos) só escondiam o botão: a URL `/novo/` aceitava POST e permitia forjar solicitação com outro solicitante ou status. Agora o servidor bloqueia com 403. |
| Solicitações de workflow | **CORRIGIDO.** Qualquer usuário via todas as solicitações (ex.: aprovações de pagamento). Agora cada um vê só as que abriu, as que estão com ele e as que já tratou. Ver tudo exige a permissão `ver_todas_instancia` (grupo Administradores). |
| Dados pessoais (LGPD) | **CORRIGIDO.** Financeiro, Fiscal e Compras tinham `core.view_*` e viam colaboradores (CPF, salário) e a auditoria (que guarda os valores alterados). Agora colaboradores ficam só com RH e Administradores, e a auditoria só com Administradores. RH perdeu `ged.*` amplo (exclusão e "ver confidenciais"). |
| Trilha de auditoria | Somente leitura, inclusive no Django Admin e para superusuários: não pode ser editada nem apagada. |
| Página Banco de dados (backup, VACUUM) | Só superusuários. VACUUM só via POST com CSRF. |
| Ações que alteram dados | Exigem POST com token CSRF. **CORRIGIDO:** "Gerar cobrança" criava registro via GET. |
| Redirecionamento após salvar | **CORRIGIDO.** O parâmetro `?next=//site-externo` permitia redirecionamento aberto (phishing). |
| Cabeçalhos | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, `COOP`, e HSTS com HTTPS. |

Grupos padrão (recriados por `python manage.py setup_erp`; ajustes manuais nos grupos são sobrescritos):

| Grupo | Acesso |
|---|---|
| Administradores | Tudo nos módulos do ERP, mais criar e alterar usuários. Banco de dados e Admin completo exigem também superusuário/staff. |
| Financeiro | Financeiro e cobrança completos, cadastros básicos (sem colaboradores) e consulta fiscal. |
| Fiscal | Fiscal completo, cadastros básicos, consulta financeira e lançamento de títulos. |
| RH | Colaboradores, departamentos, cargos e criação de pastas no GED. |
| Compras | Fornecedores, lançamento de contas a pagar e de notas. |
| Usuários | Abrir e acompanhar solicitações, consultar e enviar documentos no GED. |

## 3. Portas

| Porta | Serviço | Exposição padrão | Recomendação |
|---|---|---|---|
| **8000** | ERP (HTTP) | Rede local (`WEB_BIND=0.0.0.0`) | Libere só para a rede interna no firewall. Para senhas criptografadas, use o proxy HTTPS (abaixo) com `WEB_BIND=127.0.0.1`. |
| **5432** | PostgreSQL | **Docker: não exposta.** Linux: só `localhost` (padrão Debian/Ubuntu). Windows: o instalador avisa se estiver aberta na rede. | Nunca exponha à internet. Se o banco for só do ERP: `listen_addresses = 'localhost'`. |
| **5050** | pgAdmin | **Só `127.0.0.1`** (o próprio servidor) | Para acessar de outro PC, prefira túnel SSH (`ssh -L 5050:localhost:5050 servidor`). Se precisar liberar: `PGADMIN_BIND=0.0.0.0` + firewall restrito ao IP do administrador. |
| **443 / 80** | Caddy (HTTPS, opcional) | Desligado. `docker compose --profile https up -d` | Com domínio próprio, troque `:443` pelo domínio no `docker/Caddyfile` para certificado Let's Encrypt. Depois: `DJANGO_HTTPS=1`, `CONFIAR_X_FORWARDED_FOR=1`, `WEB_BIND=127.0.0.1`. |

Nenhuma porta deve ser redirecionada no roteador para a internet sem HTTPS **e** VPN ou restrição de IP.

Os instaladores ajudam nisso:

- Linux: libera só a 8000 no `ufw`, deixa o `.env` com `chmod 600` e roda os serviços systemd com `NoNewPrivileges` e `ProtectSystem`.
- Windows: cria a regra de firewall da 8000 só para os perfis Domínio e Privado e restringe o `.env` ao usuário.

## 4. Manutenção

- **Backup:** botão na página Banco de dados (superusuário) ou `docker compose exec db pg_dump -U postgres erp > backup.sql`.
  O arquivo contém **todos os dados**, inclusive pessoais: guarde criptografado e fora do servidor.
- **VACUUM/ANALYZE:** o autovacuum do PostgreSQL já roda sozinho. O botão força uma execução manual.
- **Atualizações:** `git pull && docker compose up -d --build` (aplica migrações automaticamente).
  Atualize as imagens (`docker compose pull`) mensalmente.
- **Após a instalação:** troque a senha do administrador pela tela, apague `DJANGO_SUPERUSER_PASSWORD`
  do `.env` e **nunca** use `CARREGAR_DEMO=1` em produção (ele cria usuários com senha conhecida).

## 5. Pendências recomendadas (fora do escopo desta versão)

- Autenticação em dois fatores (ex.: `django-otp`) para Administradores e Financeiro.
- Integração com AD/LDAP ou SSO, se a empresa já usa.
- Antivírus nos uploads do GED (ex.: ClamAV).
- Backup automático agendado com retenção e cópia externa.
- Teste de invasão por terceiro antes de expor o sistema à internet.
