@echo off
REM ============================================================================
REM  TGClassPlayer v6 - Gerador do executavel (.exe) para Windows
REM ----------------------------------------------------------------------------
REM  Basta dar DUPLO CLIQUE neste arquivo (ou rodar no Prompt de Comando).
REM  Requisitos: Python 3.10/3.11/3.12 (64 bits) instalado e no PATH.
REM ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo   TGClassPlayer v6 - Build do executavel (.exe)
echo ============================================================
echo.

REM ---- 1) Verifica o Python -------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no PATH.
    echo        Instale o Python 3.10/3.11/3.12 em https://www.python.org/downloads/
    echo        e MARQUE a opcao "Add Python to PATH" durante a instalacao.
    echo.
    pause
    exit /b 1
)

python -c "import sys; print('Python', sys.version)"
echo.

REM ---- 2) Cria/usa ambiente virtual ----------------------------------------
if not exist ".venv" (
    echo [1/4] Criando ambiente virtual (.venv) ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Ambiente virtual ja existe ^(.venv^).
)

call ".venv\Scripts\activate.bat"

REM ---- 3) Instala dependencias ---------------------------------------------
echo.
echo [2/4] Atualizando pip e instalando dependencias ...
python -m pip install --upgrade pip setuptools wheel

REM Instala as dependencias OBRIGATORIAS. PySide6 ja inclui QtMultimedia,
REM QtWebEngine e demais "Addons" (nao instale PySide6-Addons separado).
python -m pip install "PySide6>=6.6,<6.9" "Pyrogram==2.0.106" "aiohttp>=3.9" "pyinstaller>=6.3"
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias obrigatorias.
    echo        Verifique sua conexao com a internet e a versao do Python.
    pause
    exit /b 1
)

REM TgCrypto e OPCIONAL (so acelera). Se falhar (ex.: Python 3.13 sem wheel),
REM o build CONTINUA normalmente — o app funciona sem ele.
echo.
echo [2/4] Instalando TgCrypto (opcional, acelera o streaming) ...
python -m pip install "TgCrypto>=1.2.5"
if errorlevel 1 (
    echo [AVISO] Nao foi possivel instalar o TgCrypto. Sem problema:
    echo         o aplicativo funciona mesmo assim ^(apenas um pouco mais lento^).
)

REM ---- 4) Limpa builds anteriores ------------------------------------------
echo.
echo [3/4] Limpando builds anteriores ...
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

REM ---- 5) Gera o executavel -------------------------------------------------
echo.
echo [4/4] Gerando o executavel com PyInstaller ^(pode demorar alguns minutos^) ...
echo.
python -m PyInstaller --noconfirm TGClassPlayer.spec
if errorlevel 1 (
    echo.
    echo [ERRO] A geracao do executavel falhou. Veja as mensagens acima.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   SUCESSO!
echo ============================================================
echo.
echo   O aplicativo foi gerado em:
echo       dist\TGClassPlayer\TGClassPlayer.exe
echo.
echo   Para distribuir, copie a PASTA INTEIRA "dist\TGClassPlayer".
echo.
pause
endlocal
