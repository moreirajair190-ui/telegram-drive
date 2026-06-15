@echo off
REM ============================================================================
REM  TgPlayer v6.4 - Rodar a partir do codigo-fonte (sem gerar .exe)
REM  Util para testar rapidamente. Cria o .venv na primeira execucao.
REM
REM  A janela NUNCA fecha sozinha: qualquer erro mostra a causa e aguarda uma
REM  tecla (pause no final). Se ainda assim sumir, rode pelo CMD na pasta.
REM ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

call :main
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
    echo ============================================================
    echo   [ERRO] O TgPlayer terminou com erro ^(codigo %RC%^).
    echo   Veja as mensagens ACIMA.
    echo ============================================================
)
echo.
pause
endlocal & exit /b %RC%

REM ===========================================================================
:main
where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH. Instale o Python 3.10/3.11/3.12.
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo Criando ambiente virtual ^(.venv^) ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        exit /b 2
    )
    call ".venv\Scripts\activate.bat"
    if errorlevel 1 (
        echo [ERRO] Falha ao ativar o .venv.
        exit /b 2
    )
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERRO] Falha ao instalar as dependencias ^(requirements.txt^).
        exit /b 3
    )
) else (
    call ".venv\Scripts\activate.bat"
    if errorlevel 1 (
        echo [ERRO] Falha ao ativar o .venv.
        exit /b 2
    )
)

echo Iniciando o TgPlayer ...
python TgPlayer.py
if errorlevel 1 exit /b 4
exit /b 0
