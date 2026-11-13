# Guia de instalação e requisitos

Este guia cobre a instalação do **Guará ERP** em servidores **Linux** e **Windows**, com Docker
(recomendado) ou sem Docker. Também traz o acesso pela rede, HTTPS, backup, atualização e
solução de problemas. Para as decisões de segurança, veja [SEGURANCA.md](../SEGURANCA.md).

## 1. Requisitos

### 1.1 Servidor (hardware)

| Porte da empresa | Usuários simultâneos | CPU | Memória RAM | Disco |
|---|---|---|---|---|
| Pequena | até 15 | 2 núcleos | 4 GB | 40 GB SSD |
| Média | até 60 | 4 núcleos | 8 GB | 100 GB SSD |
| Maior | 60 ou mais | 8 núcleos | 16 GB | 200 GB SSD + disco de backup |

O espaço em disco cresce principalmente com os arquivos do **GED** e os XMLs fiscais. Reserve
o mesmo tamanho em outro disco ou NAS para os backups.

### 1.2 Sistema operacional

| Sistema | Versões testadas/suportadas | Modo |
|---|---|---|
| Ubuntu Server | 22.04 LTS, 24.04 LTS | Docker ou nativo (script) |
| Debian | 12 | Docker ou nativo (script) |
| Outras distribuições (RHEL, Rocky, Alma) | 9 | Docker |
| Windows Server | 2019, 2022, 2025 | Nativo (script) ou Docker Desktop/WSL2 |
| Windows 10/11 Pro | 22H2 ou superior | Nativo (script) ou Docker Desktop |
| Máquina virtual (Proxmox, Hyper-V, VMware) | — | Recomendado: VM Ubuntu Server + Docker |

### 1.3 Software

**Com Docker (recomendado):**

| Componente | Versão mínima |
|---|---|
| Docker Engine (Linux) ou Docker Desktop (Windows) | 24 |
| Docker Compose (plugin `docker compose`) | 2.20 |
| Git (para baixar e atualizar o código) | 2.30 |

O Docker baixa e configura sozinho o restante: Python 3.12, PostgreSQL 16, pgAdmin 4 e, opcionalmente, o Caddy para HTTPS.

**Sem Docker (instalação nativa):**

| Componente | Versão |
|---|---|
| Python | 3.11, 3.12 ou 3.13 |
| PostgreSQL | 14, 15 ou 16 (recomendado 16) |
| Cliente PostgreSQL (`pg_dump`) | mesma versão do servidor, para o backup pela tela |
| Git | 2.30 ou mais recente |

As bibliotecas Python ficam em `requirements.txt`: Django 5.1, psycopg 3, django-filter, whitenoise,
gunicorn (Linux), waitress (Windows), python-dotenv, tzdata, defusedxml, cryptography e **pyHanko**
(assinatura digital PAdES/CAdES).

**Assinatura digital (opcional):**

- Os usuários precisam de certificado **ICP-Brasil A1** (`.pfx`) para assinar pelo sistema. Quem usa
  certificado **A3** (token ou cartão) assina no próprio computador e envia o PDF assinado.
- Para a tela de verificação marcar a cadeia como confiável, o servidor precisa dos certificados raiz da
  ICP-Brasil (seção 2.8).

### 1.4 Estações dos usuários

- Navegador atualizado: **Chrome, Edge, Firefox ou Safari** (últimas 2 versões).
- Resolução mínima de 1366×768. Também funciona em tablets e celulares.
- Nenhuma instalação nos computadores dos usuários.
- Não é preciso internet: as bibliotecas de interface vêm junto com o sistema. A única exceção é a
  busca automática de CEP (ViaCEP), que é opcional.

### 1.5 Rede

| Porta | Uso | Quem precisa acessar |
|---|---|---|
| 8000/TCP | ERP (HTTP) | Computadores dos usuários |
| 443/TCP e 80/TCP | ERP com HTTPS (opcional, proxy Caddy) | Computadores dos usuários |
| 5050/TCP | pgAdmin | Só o próprio servidor (padrão) |
| 5432/TCP | PostgreSQL | Ninguém fora do servidor |
| 587/TCP de saída | Envio de e-mails da régua de cobrança (SMTP) | Servidor → provedor de e-mail |

Detalhes e recomendações de firewall estão em [SEGURANCA.md, seção Portas](../SEGURANCA.md#3-portas).

---

## 2. Instalação com Docker (Linux ou Windows)

### 2.1 Preparar o servidor

**Ubuntu/Debian:**

```bash
sudo apt update && sudo apt install -y git ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # saia e entre de novo na sessão
```

**Windows:** instale o [Docker Desktop](https://www.docker.com/products/docker-desktop/) com o
backend WSL2 e o [Git para Windows](https://git-scm.com/download/win). Use o **PowerShell** nos comandos.

### 2.2 Baixar o sistema

```bash
git clone https://github.com/raldineybsantos/guara-erp.git
cd guara-erp
```

### 2.3 Configurar o `.env`

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

Edite o `.env` e troque **obrigatoriamente**:

| Variável | O que colocar |
|---|---|
| `DJANGO_SECRET_KEY` | Chave aleatória: `python3 -c "import secrets; print(secrets.token_urlsafe(50))"` (ou qualquer texto aleatório com 50+ caracteres) |
| `POSTGRES_PASSWORD` | Senha do usuário `erp` do banco (o ERP usa este usuário) |
| `POSTGRES_ADMIN_PASSWORD` | Senha do superusuário `postgres` (só pgAdmin e manutenção) |
| `PGADMIN_PASSWORD` | Senha de entrada no pgAdmin |
| `DJANGO_SUPERUSER_USERNAME` / `_PASSWORD` | Primeiro administrador do ERP |
| `DJANGO_ALLOWED_HOSTS` | Endereços usados no navegador, ex.: `localhost,192.168.0.10,erp.empresa.local` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Os mesmos endereços com protocolo e porta, ex.: `http://192.168.0.10:8000` |

Opcional: os dados de **e-mail** (`EMAIL_HOST`, `EMAIL_HOST_USER`...) para a régua de cobrança enviar
mensagens. Sem eles, os e-mails só aparecem no log.

> Use senhas diferentes e fortes (12 ou mais caracteres). Não use `CARREGAR_DEMO=1` em produção.

### 2.4 Subir

```bash
docker compose up -d --build
docker compose ps                   # todos os serviços devem estar "running"/"healthy"
docker compose logs -f web          # acompanhar a inicialização (Ctrl+C para sair)
```

Na primeira subida, o sistema faz sozinho:

1. Cria o banco `erp` e o usuário `erp` **sem privilégios de superusuário**.
2. Aplica a estrutura das tabelas (migrações).
3. Cria os grupos de acesso, o plano de contas, a régua de cobrança, os processos e as pastas padrão.
4. Cria o administrador definido no `.env`.

Acesse **http://IP-DO-SERVIDOR:8000** e entre com o administrador.

### 2.5 Pós-instalação (obrigatório)

1. Troque a senha do administrador pela tela (menu do usuário → **Alterar senha**).
2. Apague a linha `DJANGO_SUPERUSER_PASSWORD` do `.env`.
3. Restrinja o arquivo: `chmod 600 .env` (Linux).
4. Siga os [Primeiros passos do manual](MANUAL.md#2-primeiros-passos-administrador).

### 2.6 HTTPS (recomendado)

Sem HTTPS, as senhas trafegam sem criptografia na rede. Para ativar:

1. **Rede interna** (certificado próprio do Caddy; o navegador avisará na primeira vez): não é preciso alterar nada.
   **Domínio público** apontando para o servidor: no `docker/Caddyfile`, troque `:443` pelo domínio
   (ex.: `erp.suaempresa.com.br {`) e apague a linha `tls internal`. O certificado Let's Encrypt é automático.
2. No `.env`: `DJANGO_HTTPS=1`, `CONFIAR_X_FORWARDED_FOR=1`, `WEB_BIND=127.0.0.1` e
   `DJANGO_CSRF_TRUSTED_ORIGINS=https://erp.suaempresa.com.br`.
3. Suba com o perfil: `docker compose --profile https up -d`.

### 2.7 Rotinas diárias

O serviço `agendador` (Docker) ou `erp-agendador` (Linux) executa todo dia às 08:00:

- a **régua de cobrança**;
- os **alertas de contratos a vencer**, que viram cartões no quadro de tarefas;
- a **renovação automática** de contratos.

No Windows, agende `scripts\regua_windows.bat` no Agendador de Tarefas. Esse arquivo roda as duas rotinas.

### 2.8 Assinatura digital: cadeia ICP-Brasil e carimbo do tempo

1. Baixe os certificados das **ACs Raiz da ICP-Brasil** (v5, v10, v11...) no repositório do ITI:
   https://www.gov.br/iti/pt-br/assuntos/repositorio
2. Copie os arquivos `.crt`, `.cer` ou `.pem` para a pasta `certificados/icp-brasil/` do projeto (ou
   defina outra pasta em `ICP_BRASIL_CAS_DIR` no `.env`).
3. Reinicie o ERP. Na tela **Verificar assinaturas**, a coluna *Cadeia ICP-Brasil* passa a mostrar *Confiável*.

Opcional, carimbo do tempo: informe em `ASSINATURA_TSA_URL` o endereço de uma Autoridade de Carimbo do
Tempo (RFC 3161). As assinaturas PAdES passam a levar a data e hora certificadas por terceiros.

**Para testar sem certificado real**, gere um certificado de teste. Ele é marcado como "NÃO É
ICP-Brasil" e não tem validade jurídica:

```bash
docker compose exec web python manage.py gerar_certificado_teste --nome "Seu Nome" --cpf 00000000000 --senha teste123 --saida /tmp/teste.pfx
docker compose cp web:/tmp/teste.pfx .
```

Use o CPF que está no seu cadastro de colaborador.

### 2.9 Comandos do dia a dia

| Ação | Comando |
|---|---|
| Parar | `docker compose stop` |
| Iniciar | `docker compose start` |
| Ver logs | `docker compose logs -f web` |
| Comando de administração | `docker compose exec web python manage.py <comando>` |
| Criar outro superusuário | `docker compose exec web python manage.py createsuperuser` |
| Executar a régua agora | `docker compose exec web python manage.py executar_regua` |
| Gerar alertas de contratos agora | `docker compose exec web python manage.py alertar_contratos` |

---

## 3. Instalação sem Docker — Linux (Ubuntu/Debian)

```bash
git clone https://github.com/raldineybsantos/guara-erp.git
cd guara-erp
sudo ./scripts/instalar_linux.sh
```

O script:

- instala Python, PostgreSQL e o cliente `pg_dump`;
- cria o `.env` com chave secreta e senha do banco aleatórias, já com o IP do servidor;
- cria o usuário `erp` do banco **sem privilégios** e o banco `erp`;
- aplica as migrações e a configuração inicial (`setup_erp`);
- registra os serviços systemd `erp-web` (gunicorn, porta 8000) e `erp-agendador` (régua de cobrança e
  contratos, todo dia às 08:00);
- libera a porta 8000 no `ufw`, se ele estiver ativo.

Ao final, crie o administrador:

```bash
sudo -u $USER .venv/bin/python manage.py createsuperuser
```

Gerenciar os serviços:

```bash
sudo systemctl status erp-web erp-agendador
sudo systemctl restart erp-web
sudo journalctl -u erp-web -f
```

Para HTTPS sem Docker, coloque um **nginx** ou **Caddy** na frente da porta 8000 e configure no `.env`:
`DJANGO_HTTPS=1` e `CONFIAR_X_FORWARDED_FOR=1`. Depois, faça o gunicorn escutar só em `127.0.0.1:8000`,
editando `/etc/systemd/system/erp-web.service`.

---

## 4. Instalação sem Docker — Windows

### 4.1 Pré-requisitos

1. **Python 3.12** em [python.org](https://www.python.org/downloads/windows/). Na instalação, marque
   **"Add python.exe to PATH"**.
2. **PostgreSQL 16** em [postgresql.org](https://www.postgresql.org/download/windows/) (instalador EDB).
   Anote a senha do usuário `postgres`. Durante a instalação, deixe o *Stack Builder* desmarcado.
3. Adicione `C:\Program Files\PostgreSQL\16\bin` ao **PATH** do sistema, para que o `psql` e o `pg_dump`
   funcionem (Painel de Controle → Sistema → Variáveis de ambiente).
4. **Git para Windows** (opcional, para atualizar com `git pull`).

### 4.2 Instalar

Abra o **PowerShell como Administrador** na pasta do projeto:

```powershell
git clone https://github.com/raldineybsantos/guara-erp.git
cd guara-erp
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\instalar_windows.ps1
```

1. Na primeira execução, o script cria o `.env` com uma chave aleatória e abre o arquivo no Bloco de Notas.
   Defina `POSTGRES_PASSWORD` (a senha que o ERP vai usar) e, se precisar, os endereços em
   `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS`. Salve.
2. Rode o script **de novo**. Ele:
   - cria o usuário e o banco no PostgreSQL (pede a senha do `postgres`);
   - aplica a estrutura e a configuração inicial;
   - cria a regra de firewall da porta 8000 só para as redes **Privada/Domínio**;
   - restringe o `.env` ao seu usuário;
   - pede os dados do primeiro administrador.
3. Se o script avisar que o PostgreSQL está escutando na rede (`listen_addresses = '*'`), edite
   `C:\Program Files\PostgreSQL\16\data\postgresql.conf` para `listen_addresses = 'localhost'` e
   reinicie o serviço **postgresql-x64-16**.

### 4.3 Iniciar

```powershell
.\scripts\iniciar_windows.bat
```

O ERP fica em **http://localhost:8000** (e em http://IP-DO-SERVIDOR:8000 na rede).

**Para iniciar junto com o Windows:**

1. Abra o **Agendador de Tarefas** e crie uma tarefa com o gatilho **"Ao iniciar o computador"**.
2. Configure a ação para executar `scripts\iniciar_windows.bat`.
3. Marque **"Executar estando o usuário conectado ou não"**.

Outra opção é registrar como serviço com o [NSSM](https://nssm.cc/):
`nssm install GuaraERP "C:\caminho\guara-erp\scripts\iniciar_windows.bat"`.

**Rotinas diárias:** no Agendador de Tarefas, crie uma tarefa diária às 08:00 que execute
`scripts\regua_windows.bat`. Esse arquivo roda a régua de cobrança e os alertas e renovações de contratos.

---

## 5. Backup e restauração

**Pela tela:** Administração → Banco de dados → **Baixar backup (.sql)**. Só superusuários veem essa opção.

**Pelo terminal:**

```bash
# Docker
docker compose exec -T db pg_dump -U postgres -Fc erp > erp_$(date +%F).dump
docker run --rm -v guara-erp_media:/m -v $PWD:/b alpine tar czf /b/media_$(date +%F).tgz -C /m .

# Linux nativo
sudo -u postgres pg_dump -Fc erp > erp_$(date +%F).dump
tar czf media_$(date +%F).tgz media/
```

```powershell
# Windows nativo
pg_dump -U postgres -h localhost -Fc erp -f "erp_$(Get-Date -f yyyy-MM-dd).dump"
Compress-Archive media "media_$(Get-Date -f yyyy-MM-dd).zip"
```

> Faça backup **do banco e da pasta `media`**: os arquivos do GED e os XMLs ficam nela.
> Guarde as cópias criptografadas e fora do servidor (NAS, outro local ou nuvem). Teste a restauração periodicamente.

**Restaurar** (em um banco vazio):

```bash
# Docker
docker compose exec -T db pg_restore -U postgres -d erp --clean --if-exists --no-owner --role=erp < erp_AAAA-MM-DD.dump
# Nativo
sudo -u postgres pg_restore -d erp --clean --if-exists --no-owner --role=erp erp_AAAA-MM-DD.dump
```

Backup automático diário no Linux (crontab do root, mantém 30 dias):

```cron
0 2 * * * cd /opt/guara-erp && docker compose exec -T db pg_dump -U postgres -Fc erp > /backup/erp_$(date +\%F).dump && find /backup -name 'erp_*.dump' -mtime +30 -delete
```

---

## 6. Atualização

```bash
# faça backup antes!
cd guara-erp
git pull
docker compose up -d --build                       # Docker: migrações aplicadas sozinhas
```

**Nativo (Linux):**

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py setup_erp
.venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart erp-web erp-agendador
```

**Nativo (Windows):** pare o `iniciar_windows.bat`, rode `git pull` e execute de novo `instalar_windows.ps1`.

> O `setup_erp` recria as permissões dos grupos padrão. Para personalizar permissões, crie
> **grupos novos** em vez de alterar os padrão.

---

## 7. Solução de problemas

| Sintoma | Causa provável | Solução |
|---|---|---|
| `ImproperlyConfigured: Defina DJANGO_SECRET_KEY...` | Chave secreta ausente ou fraca | Gere uma chave com 50 caracteres no `.env` |
| "Bad Request (400)" ao abrir pelo IP | Endereço fora de `DJANGO_ALLOWED_HOSTS` | Inclua o IP/nome no `.env` e reinicie |
| "Verificação CSRF falhou" ao salvar/entrar | Origem fora de `DJANGO_CSRF_TRUSTED_ORIGINS` | Inclua `http(s)://endereço:porta` e reinicie |
| "Muitas tentativas sem sucesso" no login | Bloqueio após 5 senhas erradas | Aguarde 15 min ou ajuste `LOGIN_MAX_TENTATIVAS` |
| Tela sem estilo (sem cores/ícones) | Arquivos estáticos não coletados | `python manage.py collectstatic --noinput` |
| "connection refused" ao banco | PostgreSQL parado ou host errado | Docker: `docker compose ps`. Nativo: confira o serviço e `POSTGRES_HOST=localhost` |
| "password authentication failed for user erp" | Senha do `.env` diferente da do banco | `ALTER ROLE erp PASSWORD '...'` como `postgres` |
| Senha nova não vale no Docker depois de mudar o `.env` | O usuário do banco só é criado na 1ª subida | Altere a senha pelo pgAdmin (`ALTER ROLE erp PASSWORD`) |
| Botão "Baixar backup" indisponível | `pg_dump` fora do PATH | Instale o cliente PostgreSQL / adicione `...\PostgreSQL\16\bin` ao PATH |
| E-mails da régua não chegam | SMTP não configurado | Preencha `EMAIL_*` no `.env`. Veja o motivo da falha em Cobrança → Histórico de envios |
| "O CPF do certificado não é o mesmo do seu cadastro" | Usuário ligado a outro colaborador, ou certificado de outra pessoa | Confira o campo *Usuário do sistema* no cadastro do colaborador |
| Assinatura "Cadeia ICP-Brasil: Não verificada" | Raízes ICP-Brasil não instaladas | Seção 2.8 |
| "Não vejo a aba Minha equipe" | Usuário sem vínculo de gestor | Preencha *Gestor* nos colaboradores ou *Responsável* no departamento |
| Página Banco de dados mostra "superusuário" em vermelho | O ERP está conectando como superusuário | Crie um usuário sem privilégios, conforme [SEGURANCA.md](../SEGURANCA.md) |

Logs: `docker compose logs web` (Docker), `journalctl -u erp-web` (Linux) ou a própria janela do
`iniciar_windows.bat` (Windows).
