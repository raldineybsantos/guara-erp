@echo off
REM Rotinas diarias: regua de cobranca e alertas/renovacao de contratos.
REM Agende no "Agendador de Tarefas" do Windows para rodar todo dia as 08:00.
cd /d "%~dp0\.."
.venv\Scripts\python.exe manage.py executar_regua >> rotinas.log 2>&1
.venv\Scripts\python.exe manage.py alertar_contratos >> rotinas.log 2>&1
.venv\Scripts\python.exe manage.py rotinas_diarias >> rotinas.log 2>&1
