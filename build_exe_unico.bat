@echo off
REM ==========================================================================
REM  TgPlayer v6.4.15 - Build alternativo ONEFILE
REM --------------------------------------------------------------------------
REM  Gera um unico TgPlayer.exe em dist_onefile\.
REM  Observacao: para apps PySide6, o modo portavel em pasta (build_exe.bat)
REM  costuma ser mais confiavel. Use este apenas se voce realmente quiser
REM  um unico arquivo.
REM ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "LOG=%~dp0build_onefile_log.txt"
echo TgPlayer onefile build iniciado em %DATE% %TIME% > "%LOG%"

echo.
echo ============================================================
echo   TgPlayer v6.4.15 - Gerar EXE unico experimental
echo ============================================================
echo.

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
    echo [ERRO] Python 3.10/3.11/3.12 64 bits nao encontrado.
    pause
    exit /b 1
)

if /I "%1"=="--clean" if exist ".venv-build" rmdir /s /q ".venv-build"
if not exist ".venv-build\Scripts\python.exe" cmd /c %PYCMD% -m venv .venv-build
set "VPY=.venv-build\Scripts\python.exe"

%VPY% -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
if errorlevel 1 goto :fail
%VPY% -m pip install --disable-pip-version-check --prefer-binary -r requirements.txt
if errorlevel 1 goto :fail
%VPY% -m pip install --disable-pip-version-check --prefer-binary --upgrade "pyinstaller>=6.3"
if errorlevel 1 goto :fail

if exist build_onefile_intermediario_NAO_EXECUTAR rmdir /s /q build_onefile_intermediario_NAO_EXECUTAR
if exist dist_onefile rmdir /s /q dist_onefile

%VPY% -m PyInstaller --clean --noconfirm --onefile --windowed --name TgPlayer --icon assets\icon.ico --paths src --workpath build_onefile_intermediario_NAO_EXECUTAR --distpath dist_onefile TgPlayer.py
if errorlevel 1 goto :fail

if not exist "dist_onefile\TgPlayer.exe" goto :fail

echo.
echo [SUCESSO] EXE unico criado em: dist_onefile\TgPlayer.exe
echo Observacao: se esse EXE unico demorar para abrir, use a versao portavel em pasta do build_exe.bat.
start "" "dist_onefile"
pause
exit /b 0

:fail
echo.
echo [ERRO] Build onefile falhou. Use o build principal: build_exe.bat --clean
echo Veja o log: %LOG%
pause
exit /b 1
