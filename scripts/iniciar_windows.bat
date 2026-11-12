@echo off
REM Inicia o ERP Empresa no Windows (servidor de producao waitress).
REM Acesse em http://localhost:8000  (ou http://IP-DO-SERVIDOR:8000 na rede)
cd /d "%~dp0\.."
echo ERP Empresa em http://localhost:8000  (Ctrl+C para parar)
.venv\Scripts\waitress-serve.exe --listen=0.0.0.0:8000 --threads=8 config.wsgi:application
