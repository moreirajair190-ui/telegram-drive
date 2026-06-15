@echo off
REM ============================================================================
REM  TGClassPlayer v6 - Rodar a partir do codigo-fonte (sem gerar .exe)
REM  Util para testar rapidamente. Cria o .venv na primeira execucao.
REM ============================================================================

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH. Instale o Python 3.10/3.11/3.12.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Criando ambiente virtual ^(.venv^) ...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

echo Iniciando o TGClassPlayer ...
python TGClassPlayer.py

pause
endlocal
