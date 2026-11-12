@echo off
REM Executa a regua de cobranca. Agende no "Agendador de Tarefas" do Windows para rodar diariamente.
cd /d "%~dp0\.."
.venv\Scripts\python.exe manage.py executar_regua >> regua.log 2>&1
