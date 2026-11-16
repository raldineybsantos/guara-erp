# Segurança do Guará ERP

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

### Assinatura digital (certificado ICP-Brasil)

| Item | Como é tratado |
|---|---|
| Arquivo do certificado (.pfx) e senha | Lidos **só em memória** durante a requisição: a tela de assinatura desliga o armazenamento temporário em disco do upload. Nunca são gravados em disco, banco ou log, e são descartados ao fim da operação (há teste automatizado para isso). Limite de 100 KB. |
| Assinar em nome de outra pessoa | Bloqueado: o CPF do e-CPF precisa ser igual ao do colaborador ligado ao usuário. O e-CNPJ exige a permissão `ged.assinar_ecnpj` e um CNPJ de empresa cadastrada. |
| Certificado vencido ou sem uso para assinatura | Recusado. |
| Arquivo trocado no servidor | Antes de assinar, o SHA-256 do arquivo é comparado com o registrado no envio. |
| Integridade depois da assinatura | PAdES/CAdES com SHA-256. Qualquer alteração aparece como *Inválida / alterada* na verificação. |
| Rastreabilidade | Cada assinatura registra titular, CPF/CNPJ, autoridade certificadora, número de série, validade, motivo, IP e data. O registro é somente leitura e também vai para a auditoria. |
| Cadeia de confiança | Validada contra as raízes da ICP-Brasil colocadas em `ICP_BRASIL_CAS_DIR`. Sem elas, a assinatura aparece como "não verificada" em vez de "confiável". |
| Certificados de teste | `gerar_certificado_teste` cria certificados identificados como "NAO E ICP-Brasil", que nunca são marcados como ICP-Brasil. |

### Tarefas e contratos

- O quadro Kanban só mostra atividades da própria pessoa, dos seus grupos e da equipe que ela gerencia.
  O servidor confere isso também ao mover cartões: atividade não visível responde 404, e coluna inválida responde 400.
- Os termos de contratos aprovados (partes, valores, vigência) ficam travados. A mudança passa por
  aditivo, que fica registrado.
- A pasta *Jurídico* do GED é restrita aos grupos Jurídico e Administradores. Enviar contrato para
  aprovação e registrar assinatura exigem a permissão `juridico.aprovar_contrato`.

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

- **Backup:** automático e cifrado (veja "Verificação em duas etapas e backup" abaixo). O botão `.sql` da página
  Banco de dados gera um arquivo **sem cifra** com **todos os dados**, inclusive pessoais: use com cuidado.
- **VACUUM/ANALYZE:** o autovacuum do PostgreSQL já roda sozinho. O botão força uma execução manual.
- **Atualizações:** `git pull && docker compose up -d --build` (aplica migrações automaticamente).
  Atualize as imagens (`docker compose pull`) mensalmente.
- **Após a instalação:** troque a senha do administrador pela tela, apague `DJANGO_SUPERUSER_PASSWORD`
  do `.env` e **nunca** use `CARREGAR_DEMO=1` em produção (ele cria usuários com senha conhecida).

## Auditoria e logs de erro

- **Trilha imutável:** toda inclusão/alteração/exclusão, login, exportação, leitura de dados sensíveis, acesso negado e mudança de permissões é gravada com usuário, IP, requisição e valores antes/depois. O ORM e gatilhos do PostgreSQL bloqueiam `UPDATE`/`DELETE`; o sistema nunca apaga a trilha.
- **Evidência de adulteração:** hash SHA-256 por registro e selos encadeados; `python manage.py auditoria_verificar` (ou a tela *Auditoria → Integridade*) detecta alteração ou remoção. Quem tem acesso de dono ao banco pode derrubar os gatilhos, mas não consegue esconder: a cadeia quebra. Mantenha cópias externas (`auditoria_exportar`, com `.sha256`).
- **Senhas e segredos** nunca entram na trilha nem nos logs de erro (mascarados).
- **Perfil Auditor:** grupo *Auditoria*, somente leitura.
- **Logs de erro:** agrupados, com código de requisição exibido ao usuário, notificação ao administrador e retenção `LOG_ERRO_RETENCAO_DIAS`.

## RH e folha de pagamento

- **Salários e holerites são dados pessoais sensíveis:** só quem tem `rh.view_folha` (RH, Administradores, Auditoria) vê todos;
  cada colaborador vê apenas o próprio holerite e só depois de a folha ser fechada. Cada consulta fica na trilha.
- **Ponto:** as marcações não são editadas nem apagadas (correções entram como lançamento manual justificado) e cada
  colaborador só vê o próprio espelho (quem tem `rh.gerir_ponto` vê todos).
- **Segregação:** quem fecha a folha (`rh.fechar_folha`) e quem efetiva rescisões (`rh.efetivar_rescisao`) são permissões
  separadas; o gestor aprova férias só dos seus liderados.
- **Certificado do eSocial:** é o mesmo A1 da configuração fiscal, guardado cifrado; o XML assinado fica no banco, mas
  o certificado e a senha nunca entram na trilha de auditoria.

## 5. Pendências recomendadas (fora do escopo desta versão)

- Antivírus nos uploads do GED (ex.: ClamAV).
- Teste de invasão por terceiro antes de expor o sistema à internet.

## Verificação em duas etapas e backup

**2FA (TOTP).** Código de 6 dígitos de aplicativo autenticador (Google/Microsoft Authenticator, Authy, Bitwarden,
1Password), implementado conforme a RFC 6238 e conferido com os vetores de teste da própria RFC.

- **Quem:** obrigatório para superusuários e para os grupos de `MFA_GRUPOS_OBRIGATORIOS` (padrão: Administradores e
  Financeiro); os demais podem ativar em *Segurança da conta*. Quem é obrigado e ainda não cadastrou só alcança a tela
  de cadastro: um *middleware* bloqueia todo o resto até o segundo fator ser concluído nesta sessão (inclusive o `/admin/`).
- **Segredo cifrado:** o segredo TOTP fica cifrado no banco com a mesma chave do cofre do certificado e nunca aparece na
  auditoria. Cada código vale uma vez (o passo de 30 s usado é gravado e recusado de novo); tolerância de ±30 s de relógio.
- **Códigos de recuperação:** 10, uso único, guardados só como SHA-256. Ao gerar novos, os antigos deixam de valer.
- **Força bruta:** `LOGIN_MAX_TENTATIVAS` erros em `LOGIN_BLOQUEIO_MINUTOS` bloqueiam a verificação do usuário (mesmo
  com o código certo) e ficam na auditoria (`auth.mfa`). Ações sensíveis (novos códigos, desativar) pedem um código válido.
- **Celular perdido:** com um código de recuperação o usuário entra e recadastra; sem eles, um **superusuário redefine**
  em *Administração → Dois fatores* (fica registrado quem redefiniu). Se o único superusuário perder tudo:
  `python manage.py shell -c "from apps.seguranca.models import DispositivoMFA as D; D.objects.filter(usuario__username='NOME').delete()"`
  (acesso ao servidor).
- **Limite honesto:** TOTP não protege contra páginas falsas que retransmitem o código em tempo real (*phishing* ativo).
  Para isso, chaves de segurança (WebAuthn) — não implementadas. Sessões abertas antes da atualização pedem o 2FA no
  próximo acesso. `MFA_ATIVO=0` desliga tudo (use só para recuperação de emergência).

**Backup.** Cifrado com AES-256-GCM (chave derivada por scrypt da `BACKUP_SENHA`; blocos autenticados, então senha errada,
adulteração ou arquivo truncado são detectados), com permissão `0600`, verificado logo após a geração e copiado para fora
do servidor. Retenção avô-pai-filho. Nenhum comando passa por shell; o download e a execução são só de superusuário e vão
para a auditoria. **O `.env` e a `BACKUP_SENHA` não estão no backup: guarde-os em outro lugar.** Durante a geração há,
por segundos, um dump em texto claro numa pasta temporária (`0700`) que é apagada ao final — em servidor compartilhado,
aponte `TMPDIR` para um disco cifrado.

## Login corporativo (LDAP/AD e SSO)

- **LDAP/AD (ldap3):** só TLS (LDAPS ou StartTLS) com **certificado sempre validado** (CA própria opcional). A senha vazia é
  recusada (evita o bind anônimo), o login digitado entra no filtro **escapado** (sem injeção de LDAP), a conta de serviço só
  lê e sua senha fica **cifrada** com a chave do cofre e fora da auditoria. Conta desativada no AD não entra. Um usuário
  local de mesmo login não é assumido pelo diretório sem autorização explícita.
- **SSO OpenID Connect:** fluxo de código com **PKCE (S256)**, `state` e `nonce` de uso único na sessão (expira em 10 min).
  O ID token é validado por assinatura (JWKS do provedor, algoritmos assimétricos apenas: `none` e HMAC são recusados),
  emissor, público, validade e nonce; Microsoft confere ainda o tenant do token e **rejeita** `common`/`organizations`/
  `consumers`. O endereço de descoberta e todos os endpoints precisam ser HTTPS e o emissor anunciado tem de coincidir
  com o configurado.
- **Vínculo seguro:** a identidade é ligada pelo identificador estável do provedor (`sub`), nunca só pelo e-mail. Usuário local
  com o mesmo e-mail só é ligado se o administrador marcar isso; e-mail não verificado é recusado; criação automática exige
  domínio permitido (Google/genérico), cria sem acesso e nunca dá perfil de administrador.
- **Limites honestos:** (1) o ERP só consulta o diretório/provedor no login: desativar alguém lá **não derruba** a sessão
  aberta (até 8 h) — desative também no ERP; (2) o grupo vindo do LDAP é sincronizado a cada login, não em tempo real;
  (3) SAML e SCIM (provisionamento/desprovisionamento automático) não estão implementados; (4) o LDAP usa o login como
  identificador, então reaproveitar um login de alguém que saiu cria a ligação com o antigo usuário do ERP.

## Certificado A1 da empresa (transmissão fiscal)

- O `.pfx` e a senha são guardados **cifrados** (Fernet: AES-128-CBC com HMAC) no banco. A chave vem de
  `FISCAL_CHAVE_COFRE` (ou, na falta, da `DJANGO_SECRET_KEY`) e fica fora do banco: um backup do banco
  sozinho não expõe o certificado.
- O upload é lido só em memória (sem arquivo temporário), limitado a 100 KB, e exige a permissão
  `fiscal.change_configuracaofiscal` (grupos Fiscal e Administradores).
- Não há tela nem URL que devolva o certificado ou a senha. A auditoria não registra esses campos.
- O ERP recusa certificado vencido, com senha errada, que não seja e-CNPJ ou de outro CNPJ.
- **Conexão com o Emissor Nacional (TLS mútuo):** a biblioteca HTTP só aceita a chave privada em arquivo. O ERP a
  grava numa pasta temporária com permissão `0600` (em `/dev/shm`, memória, quando existe), usa durante a chamada
  e apaga em seguida, mesmo se houver erro. A verificação do servidor (HTTPS) fica sempre ligada. Há teste
  automatizado que confere permissão e remoção.
- A transmissão em **produção** é travada por `FISCAL_NFSE_NACIONAL_PRODUCAO`: ninguém emite nota real por engano
  durante os testes.
- Quem tem acesso ao servidor **e** à chave do cofre consegue assinar pela empresa. Restrinja o acesso
  ao `.env` e troque o certificado se houver suspeita de vazamento.

## Relatórios e dashboards

- Cada painel exige as permissões de leitura dos dados que mostra (as mesmas das telas de lista). Quadro
  sem permissão não é calculado nem enviado ao navegador, e o painel executivo traz só o que o perfil vê.
- **Filtro por empresa:** o parâmetro `empresa` só vale para empresas a que o usuário tem acesso. Um número
  de empresa alheio volta para "todas que eu acesso", e os números continuam recortados pelo acesso do usuário.
- No Service Desk, a equipe vê só as áreas dos seus grupos; em SST, só quem tem a permissão de ver todos os
  incidentes. A exportação CSV passa pela mesma proteção contra fórmulas das listas.

## Assistente de IA e canal Telegram

- **A IA não tem acesso de escrita nem de leitura ao banco.** Ela recebe o pedido do usuário e a lista de
  campos do formulário, e devolve um JSON. O ERP trata esse JSON como dado não confiável.
- **Consultas:** a IA devolve apenas texto de busca e filtros, escolhidos de uma lista fechada. Quem
  consulta é o ERP, com a permissão de ver, o recorte por empresa e os filtros da própria tela. As linhas
  encontradas não são enviadas ao modelo. Não há consulta pelo Telegram.
- **Triagem do Service Desk (opcional, desligada por padrão):** aqui o modelo recebe **o texto do chamado
  e os artigos da biblioteca visíveis ao solicitante** (nunca os internos). Com provedor na nuvem, esse
  conteúdo sai da empresa. O serviço vem de lista fechada, a prioridade nunca é crítica, a resposta precisa
  citar um artigo que foi oferecido, e a IA não encerra chamado: só o solicitante confirma a solução.
  Falha da IA encaminha o chamado para a equipe, e o SLA não para durante a conversa.
- **Lista fechada:** o modelo só pode escolher um cadastro que o usuário tem permissão de incluir. Qualquer
  outra resposta é descartada. Campos fora do formulário são ignorados e os valores passam pela validação
  normal (CPF/CNPJ, opções, datas).
- **Pessoa no circuito:** nada é gravado pela IA. A proposta abre o formulário normal, e quem salva é o
  usuário, com as permissões e a auditoria de sempre. Isso limita o efeito de um texto malicioso
  (injeção de instruções) a uma proposta errada, que o usuário vê antes de salvar.
- **Dados fora da URL:** o formulário é preenchido por referência à proposta (`?ia=N`), que só o dono abre.
- **Limites:** 1.500 caracteres por pedido e 30 pedidos por hora por usuário.
- **Provedor na nuvem:** o texto digitado sai da empresa. A tela avisa o usuário. Para dados que não podem
  sair, use o Ollama local.
- **Telegram:** o webhook exige o cabeçalho secreto (comparado em tempo constante) e o canal ligado; só
  conversas privadas de chats vinculados por código de uso único (10 minutos, gerado dentro do ERP) são
  atendidas; o bot não repete os dados do cadastro e não responde consultas. Quem tiver o celular
  desbloqueado de um usuário consegue criar propostas em nome dele, mas não gravar: para isso precisa
  entrar no ERP. O usuário desfaz o vínculo pelo ERP ou com `/desvincular`.
- **Segredos:** chave de API e token do bot ficam cifrados (mesmo cofre do certificado A1), fora da
  auditoria, e não voltam para a tela.
