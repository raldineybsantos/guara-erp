# Instalação do ERP Empresa no Windows (sem Docker)
# Pré-requisitos: Python 3.11+ e PostgreSQL 14+ instalados (https://www.postgresql.org/download/windows/)
# Execute no PowerShell, dentro da pasta do projeto:
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\scripts\instalar_windows.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> Criando ambiente virtual Python (.venv)" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

if (-not (Test-Path ".env")) {
    Write-Host "==> Criando .env a partir do .env.example" -ForegroundColor Cyan
    Copy-Item .env.example .env
    $chave = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 50 | ForEach-Object { [char]$_ })
    (Get-Content .env) -replace "troque-por-uma-chave-longa-e-aleatoria", $chave `
                      -replace "POSTGRES_HOST=db", "POSTGRES_HOST=localhost" | Set-Content .env
    Write-Host "Edite o arquivo .env (senha do PostgreSQL etc.) e rode este script novamente." -ForegroundColor Yellow
    notepad .env
    exit
}

Write-Host "==> Criando banco e usuário no PostgreSQL (se não existirem)" -ForegroundColor Cyan
$envs = @{}
Get-Content .env | Where-Object { $_ -match "^\s*[^#].*=" } | ForEach-Object {
    $k, $v = $_ -split "=", 2; $envs[$k.Trim()] = $v.Trim()
}
$psql = Get-Command psql -ErrorAction SilentlyContinue
if ($psql) {
    Write-Host "Informe a senha do usuário 'postgres' quando solicitado."
    $sql = "DO `$`$BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$($envs.POSTGRES_USER)') THEN CREATE ROLE $($envs.POSTGRES_USER) LOGIN PASSWORD '$($envs.POSTGRES_PASSWORD)' NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END`$`$;"
    & psql -U postgres -h localhost -c $sql
    & psql -U postgres -h localhost -tc "SELECT 1 FROM pg_database WHERE datname='$($envs.POSTGRES_DB)'" | Out-String | ForEach-Object {
        if (-not $_.Trim()) {
            & psql -U postgres -h localhost -c "CREATE DATABASE $($envs.POSTGRES_DB) OWNER $($envs.POSTGRES_USER);"
            & psql -U postgres -h localhost -c "REVOKE ALL ON DATABASE $($envs.POSTGRES_DB) FROM PUBLIC;"
            & psql -U postgres -h localhost -d $envs.POSTGRES_DB -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC; ALTER SCHEMA public OWNER TO $($envs.POSTGRES_USER);"
        }
    }
    $listen = (& psql -U postgres -h localhost -tAc "SHOW listen_addresses").Trim()
    if ($listen -ne "localhost") {
        Write-Host "ATENÇÃO: o PostgreSQL escuta em '$listen' (porta 5432 na rede)." -ForegroundColor Yellow
        Write-Host "Se o banco é só do ERP, ajuste listen_addresses = 'localhost' no postgresql.conf e reinicie o serviço." -ForegroundColor Yellow
    }
} else {
    Write-Host "psql não encontrado no PATH. Crie o banco '$($envs.POSTGRES_DB)' e o usuário '$($envs.POSTGRES_USER)' pelo pgAdmin." -ForegroundColor Yellow
}

Write-Host "==> Aplicando estrutura do banco e configuração inicial" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe manage.py migrate
& .\.venv\Scripts\python.exe manage.py setup_erp
& .\.venv\Scripts\python.exe manage.py collectstatic --noinput

Write-Host "==> Criando o administrador (se ainda não existir)" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth.models import User; import sys; sys.exit(0 if User.objects.filter(is_superuser=True).exists() else 1)"
if ($LASTEXITCODE -ne 0) { & .\.venv\Scripts\python.exe manage.py createsuperuser }

Write-Host "==> Restringindo o .env ao usuário atual" -ForegroundColor Cyan
icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host "==> Liberando a porta 8000 no Firewall do Windows (somente rede privada/domínio)" -ForegroundColor Cyan
if (-not (Get-NetFirewallRule -DisplayName "ERP Empresa 8000" -ErrorAction SilentlyContinue)) {
    try { New-NetFirewallRule -DisplayName "ERP Empresa 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 `
            -Action Allow -Profile Domain,Private | Out-Null }
    catch { Write-Host "Execute o PowerShell como Administrador para criar a regra de firewall." -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "Instalação concluída. Para iniciar: .\scripts\iniciar_windows.bat" -ForegroundColor Green
Write-Host "Opcional (dados de demonstração): .\.venv\Scripts\python.exe manage.py seed_demo"
