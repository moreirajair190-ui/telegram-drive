@echo off
REM ============================================================================
REM  TgPlayer v6.4 - Gerador do executavel (.exe) para Windows
REM ----------------------------------------------------------------------------
REM  Basta dar DUPLO CLIQUE neste arquivo (ou rodar no Prompt de Comando).
REM  Requisitos: Python 3.10/3.11/3.12 (64 bits) instalado e no PATH.
REM
REM  IMPORTANTE: a janela NUNCA fecha sozinha. Mesmo que ocorra qualquer erro
REM  (Python ausente, falha de pip, venv corrompido, falha do PyInstaller), o
REM  script mostra a causa e aguarda voce apertar uma tecla (pause no final).
REM  Se ainda assim a janela sumir, abra o CMD/PowerShell NA PASTA e rode:
REM       build_exe.bat
REM  para ver a mensagem completa.
REM ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ---- Corpo do script numa subrotina; ponto UNICO de saida com pause -------
call :main
set "RC=%errorlevel%"
echo.
echo ============================================================
if "%RC%"=="0" (
    echo   [SUCESSO] Build concluido.
    echo.
    echo   O aplicativo foi gerado em:
    echo       dist\TgPlayer\TgPlayer.exe
    echo.
    echo   Para distribuir, copie a PASTA INTEIRA "dist\TgPlayer".
) else (
    echo   [ERRO] O build falhou ^(codigo %RC%^).
    echo   Veja as mensagens ACIMA para entender a causa.
)
echo ============================================================
echo.
pause
endlocal & exit /b %RC%

REM ===========================================================================
:main
echo.
echo ============================================================
echo   TgPlayer v6.4 - Build do executavel (.exe)
echo ============================================================
echo.

REM ---- 1/5) Verifica o Python ----------------------------------------------
echo [1/5] Verificando o Python ...
where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no PATH.
    echo        Instale o Python 3.10/3.11/3.12 em https://www.python.org/downloads/
    echo        e MARQUE a opcao "Add Python to PATH" durante a instalacao.
    exit /b 1
)
python -c "import sys; print('Python', sys.version)"
if errorlevel 1 (
    echo [ERRO] O Python encontrado no PATH nao executou corretamente.
    exit /b 1
)
echo.

REM ---- 2/5) Cria/usa ambiente virtual --------------------------------------
echo [2/5] Preparando o ambiente virtual (.venv) ...
if not exist ".venv\Scripts\activate.bat" (
    echo       Criando ambiente virtual (.venv) ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        exit /b 2
    )
) else (
    echo       Ambiente virtual ja existe ^(.venv^).
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [AVISO] Falha ao ativar o .venv. Vou recria-lo do zero ...
    call :recreate_venv
    if errorlevel 1 exit /b 3
)

REM Confirma que o "python" agora aponta para DENTRO do .venv. Se o venv
REM estiver corrompido/incompleto, recria uma unica vez e tenta de novo.
for /f "delims=" %%P in ('where python 2^>nul') do (
    set "PYPATH=%%P"
    goto :pychecked
)
:pychecked
echo       python em uso: !PYPATH!
echo !PYPATH! | find /i "\.venv\" >nul
if errorlevel 1 (
    echo [AVISO] O python ativo NAO esta no .venv. Recriando o ambiente ...
    call :recreate_venv
    if errorlevel 1 exit /b 3
)
echo.

REM ---- 3/5) Instala dependencias -------------------------------------------
echo [3/5] Atualizando pip e instalando dependencias ...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [ERRO] Falha ao atualizar o pip/setuptools/wheel.
    echo        Verifique sua conexao com a internet.
    exit /b 4
)

REM Dependencias OBRIGATORIAS. PySide6 ja inclui QtMultimedia, QtWebEngine e
REM demais "Addons" (nao instale PySide6-Addons separado).
REM Pyrogram com FAIXA de versao (em vez de pino fixo) para nao quebrar quando
REM faltar wheel para a versao exata do Python instalado.
python -m pip install "PySide6>=6.6,<6.9" "Pyrogram>=2.0.106,<2.1" "aiohttp>=3.9" "pyinstaller>=6.3"
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias obrigatorias.
    echo        Verifique sua conexao com a internet e a versao do Python.
    exit /b 4
)

REM TgCrypto e OPCIONAL (so acelera). Se falhar (ex.: Python sem wheel), o
REM build CONTINUA normalmente — o app funciona sem ele.
echo.
echo       Instalando TgCrypto (opcional, acelera o streaming) ...
python -m pip install "TgCrypto>=1.2.5"
if errorlevel 1 (
    echo [AVISO] Nao foi possivel instalar o TgCrypto. Sem problema:
    echo         o aplicativo funciona mesmo assim ^(apenas um pouco mais lento^).
)
echo.

REM ---- 4/5) Limpa builds anteriores ----------------------------------------
echo [4/5] Limpando builds anteriores ...
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"
echo.

REM ---- 5/5) Gera o executavel ----------------------------------------------
echo [5/5] Gerando o executavel com PyInstaller ^(pode demorar alguns minutos^) ...
echo.
python -m PyInstaller --noconfirm TgPlayer.spec
if errorlevel 1 (
    echo.
    echo [ERRO] A geracao do executavel falhou. Veja as mensagens acima.
    exit /b 5
)

exit /b 0

REM ===========================================================================
:recreate_venv
echo       Apagando e recriando o .venv ...
if exist ".venv" rmdir /s /q ".venv"
python -m venv .venv
if errorlevel 1 (
    echo [ERRO] Falha ao recriar o ambiente virtual.
    exit /b 1
)
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERRO] Falha ao ativar o .venv recriado.
    exit /b 1
)
exit /b 0
