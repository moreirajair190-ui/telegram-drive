@echo off
REM ==========================================================================
REM  TgPlayer v6.4.15 - Build PORTAVEL para Windows
REM --------------------------------------------------------------------------
REM  IMPORTANTE:
REM    - NAO execute nem compacte a pasta build_intermediario_NAO_EXECUTAR.
REM    - O app final fica em dist\TgPlayer\TgPlayer.exe.
REM    - Para enviar a outra pessoa, envie TgPlayer_PORTABLE_PARA_ENVIAR.zip
REM      ou a pasta inteira dist\TgPlayer.
REM ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "LOG=%~dp0build_log.txt"
set "WORKDIR=build_intermediario_NAO_EXECUTAR"
set "DISTDIR=dist"
set "APPDIR=%DISTDIR%\TgPlayer"
set "FINALZIP=%~dp0TgPlayer_PORTABLE_PARA_ENVIAR.zip"

echo TgPlayer build iniciado em %DATE% %TIME% > "%LOG%"

echo.
echo ============================================================
echo   TgPlayer v6.4.15 - Gerar executavel Windows
echo ============================================================
echo Pasta do projeto: %CD%
echo Log:              %LOG%
echo.
echo Resultado correto: %APPDIR%\TgPlayer.exe
echo Pacote para enviar: TgPlayer_PORTABLE_PARA_ENVIAR.zip
echo.
echo ATENCAO: se voce executar um .exe dentro da pasta %WORKDIR%,
echo ele dara erro de python311.dll. Essa pasta e temporaria.
echo.

REM --------------------------------------------------------------------------
echo [1/9] Procurando Python 3.10/3.11/3.12 de 64 bits...
set "PYCMD="
for %%P in ("py -3.12" "py -3.11" "py -3.10" "python") do (
    cmd /c %%~P -c "import sys; raise SystemExit(0 if sys.version_info[:2] in [(3,10),(3,11),(3,12)] and sys.maxsize > 2**32 else 1)" >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        set "PYCMD=%%~P"
        goto :python_found
    )
)
:python_found
if not defined PYCMD (
    echo [ERRO] Python 3.10, 3.11 ou 3.12 de 64 bits nao encontrado.
    echo Instale pelo site python.org e marque Add Python to PATH.
    echo ERRO: Python compativel nao encontrado.>>"%LOG%"
    pause
    exit /b 1
)
echo Usando: %PYCMD%
cmd /c %PYCMD% -c "import sys, platform; print(sys.version); print(platform.architecture())"

REM --------------------------------------------------------------------------
echo.
echo [2/9] Preparando ambiente virtual .venv-build...
if /I "%1"=="--clean" (
    if exist ".venv-build" rmdir /s /q ".venv-build"
)
if not exist ".venv-build\Scripts\python.exe" (
    cmd /c %PYCMD% -m venv .venv-build
    if errorlevel 1 goto :fail_venv
) else (
    echo Reutilizando .venv-build existente. Para limpar, use: build_exe.bat --clean
)
set "VPY=.venv-build\Scripts\python.exe"
%VPY% -c "import sys; print('Python do build:', sys.executable)"
if errorlevel 1 goto :fail_venv

REM --------------------------------------------------------------------------
echo.
echo [3/9] Atualizando pip, setuptools e wheel...
%VPY% -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
if errorlevel 1 goto :fail_pip

REM --------------------------------------------------------------------------
echo.
echo [4/9] Instalando dependencias do TgPlayer...
%VPY% -m pip install --disable-pip-version-check --prefer-binary -r requirements.txt
if errorlevel 1 goto :fail_deps

REM --------------------------------------------------------------------------
echo.
echo [5/9] Instalando/atualizando PyInstaller...
%VPY% -m pip install --disable-pip-version-check --prefer-binary --upgrade "pyinstaller>=6.3"
if errorlevel 1 goto :fail_pyinstaller

REM --------------------------------------------------------------------------
echo.
echo [6/9] Limpando builds antigos...
if exist "%WORKDIR%" rmdir /s /q "%WORKDIR%"
if exist "%DISTDIR%" rmdir /s /q "%DISTDIR%"
if exist "%FINALZIP%" del /f /q "%FINALZIP%"

REM --------------------------------------------------------------------------
echo.
echo [7/9] Gerando executavel com PyInstaller...
%VPY% -m PyInstaller --clean --noconfirm --workpath "%WORKDIR%" --distpath "%DISTDIR%" TgPlayer.spec
if errorlevel 1 goto :fail_build

if not exist "%APPDIR%\TgPlayer.exe" goto :fail_missing

REM --------------------------------------------------------------------------
echo.
echo [8/9] Verificando DLL do Python no pacote final...
set "PYDLL_FOUND="
for %%F in ("%APPDIR%\_internal\python*.dll") do (
    if exist "%%~F" set "PYDLL_FOUND=1"
)
if not defined PYDLL_FOUND (
    echo Aviso: python*.dll nao encontrada em %APPDIR%\_internal.
    echo Tentando copiar a DLL do Python instalado...
    for /f "usebackq delims=" %%D in (`%VPY% -c "import sys, pathlib; p=pathlib.Path(sys.base_prefix); print(p / ('python' + str(sys.version_info.major) + str(sys.version_info.minor) + '.dll'))"`) do set "BASE_PYDLL=%%D"
    if exist "!BASE_PYDLL!" (
        if not exist "%APPDIR%\_internal" mkdir "%APPDIR%\_internal"
        copy /Y "!BASE_PYDLL!" "%APPDIR%\_internal\" >nul
    )
)
set "PYDLL_FOUND="
for %%F in ("%APPDIR%\_internal\python*.dll") do (
    if exist "%%~F" set "PYDLL_FOUND=1"
)
if not defined PYDLL_FOUND goto :fail_pydll

echo OK: DLL do Python encontrada no pacote final.

REM --------------------------------------------------------------------------
echo.
echo [9/9] Criando ZIP correto para enviar ao usuario final...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%APPDIR%' -DestinationPath '%FINALZIP%' -Force" >nul 2>nul
if exist "%FINALZIP%" (
    echo ZIP criado: %FINALZIP%
) else (
    echo Nao foi possivel criar o ZIP automaticamente. Envie a pasta inteira: %APPDIR%
)

echo.
echo ============================================================
echo [SUCESSO] Build concluido!
echo.
echo EXECUTE ESTE ARQUIVO:
echo   %APPDIR%\TgPlayer.exe
echo.
echo PARA ENVIAR AO USUARIO FINAL, envie UM destes:
echo   1^) TgPlayer_PORTABLE_PARA_ENVIAR.zip
echo   2^) ou a pasta inteira dist\TgPlayer
echo.
echo NAO envie e NAO execute:
echo   %WORKDIR%\TgPlayer\TgPlayer.exe
echo ============================================================
echo SUCESSO em %DATE% %TIME% >> "%LOG%"
start "" "%APPDIR%"
pause
exit /b 0

:fail_venv
echo [ERRO] Falha ao criar/usar o ambiente virtual .venv-build.
goto :fail_common
:fail_pip
echo [ERRO] Falha ao atualizar pip/setuptools/wheel.
goto :fail_common
:fail_deps
echo [ERRO] Falha ao instalar dependencias. Tente rodar: build_exe.bat --clean
goto :fail_common
:fail_pyinstaller
echo [ERRO] Falha ao instalar PyInstaller.
goto :fail_common
:fail_build
echo [ERRO] PyInstaller falhou.
goto :fail_common
:fail_missing
echo [ERRO] O arquivo %APPDIR%\TgPlayer.exe nao foi encontrado.
goto :fail_common
:fail_pydll
echo [ERRO] O pacote final nao contem python*.dll em %APPDIR%\_internal.
echo Isso geralmente ocorre por build incompleto, antivirus bloqueando/quarentenando,
echo ou permissao insuficiente. Tente:
echo   1^) fechar antivirus temporariamente ou liberar a pasta do projeto;
echo   2^) rodar build_exe.bat --clean;
echo   3^) instalar Python 3.11/3.12 64 bits pelo python.org.
goto :fail_common
:fail_common
echo.
echo Verifique as mensagens acima e o arquivo build_log.txt.
echo Dicas:
echo  - Use Python 3.11 ou 3.12 de 64 bits.
echo  - Feche o TgPlayer antes de gerar o EXE.
echo  - Se ja existia .venv-build, tente: build_exe.bat --clean
echo  - Nao execute nada da pasta %WORKDIR%; ela e temporaria.
echo FALHA em %DATE% %TIME% >> "%LOG%"
pause
exit /b 1
