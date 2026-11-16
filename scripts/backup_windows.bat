@echo off
REM Backup diario cifrado (banco + arquivos do GED), com retencao e copia externa (BACKUP_COPIA_DIR no .env).
REM Agende no "Agendador de Tarefas" do Windows para rodar todo dia as 02:00.
cd /d "%~dp0\.."
.venv\Scripts\python.exe manage.py backup_executar >> backup.log 2>&1
