# =============================================================================
#  TgPlayer v6.4 - Gerador do executavel (.exe) - PowerShell
# -----------------------------------------------------------------------------
#  Alternativa mais robusta ao build_exe.bat. NUNCA fecha sozinho: ao final
#  (sucesso OU erro) aguarda voce apertar Enter (Read-Host).
#
#  Como usar:
#    1) Clique com o botao direito neste arquivo -> "Executar com PowerShell"
#       (ou abra o PowerShell na pasta e rode:  .\build_exe.ps1 )
#    2) Se aparecer aviso de "ExecutionPolicy", rode uma vez:
#       powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Fail($code, $msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host "  [ERRO] $msg" -ForegroundColor Red
    Write-Host "  Veja as mensagens ACIMA para entender a causa." -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host ""
    Read-Host "Pressione Enter para fechar"
    exit $code
}

try {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "   TgPlayer v6.4 - Build do executavel (.exe)" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""

    # 1) Python -------------------------------------------------------------
    Write-Host "[1/5] Verificando o Python ..."
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Fail 1 "Python nao encontrado no PATH. Instale o Python 3.10/3.11/3.12 e marque 'Add Python to PATH'."
    }
    python -c "import sys; print('Python', sys.version)"
    if ($LASTEXITCODE -ne 0) { Fail 1 "O Python no PATH nao executou corretamente." }
    Write-Host ""

    # 2) venv ---------------------------------------------------------------
    Write-Host "[2/5] Preparando o ambiente virtual (.venv) ..."
    if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
        Write-Host "      Criando ambiente virtual (.venv) ..."
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail 2 "Falha ao criar o ambiente virtual." }
    } else {
        Write-Host "      Ambiente virtual ja existe (.venv)."
    }
    & ".\.venv\Scripts\Activate.ps1"
    $pyPath = (Get-Command python).Source
    Write-Host "      python em uso: $pyPath"
    if ($pyPath -notmatch "\\\.venv\\") {
        Write-Host "[AVISO] python ativo NAO esta no .venv. Recriando ..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail 3 "Falha ao recriar o ambiente virtual." }
        & ".\.venv\Scripts\Activate.ps1"
    }
    Write-Host ""

    # 3) dependencias -------------------------------------------------------
    Write-Host "[3/5] Atualizando pip e instalando dependencias ..."
    python -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { Fail 4 "Falha ao atualizar pip/setuptools/wheel." }

    python -m pip install "PySide6>=6.6,<6.9" "Pyrogram>=2.0.106,<2.1" "aiohttp>=3.9" "pyinstaller>=6.3"
    if ($LASTEXITCODE -ne 0) { Fail 4 "Falha ao instalar dependencias obrigatorias." }

    Write-Host "      Instalando TgCrypto (opcional) ..."
    python -m pip install "TgCrypto>=1.2.5"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[AVISO] TgCrypto nao instalado. O app funciona mesmo assim." -ForegroundColor Yellow
    }
    Write-Host ""

    # 4) limpa --------------------------------------------------------------
    Write-Host "[4/5] Limpando builds anteriores ..."
    if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
    if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
    Write-Host ""

    # 5) PyInstaller --------------------------------------------------------
    Write-Host "[5/5] Gerando o executavel com PyInstaller (pode demorar) ..."
    Write-Host ""
    python -m PyInstaller --noconfirm TgPlayer.spec
    if ($LASTEXITCODE -ne 0) { Fail 5 "A geracao do executavel falhou. Veja as mensagens acima." }

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "   [SUCESSO] Build concluido." -ForegroundColor Green
    Write-Host "   O aplicativo foi gerado em: dist\TgPlayer\TgPlayer.exe" -ForegroundColor Green
    Write-Host "   Para distribuir, copie a PASTA INTEIRA 'dist\TgPlayer'." -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Read-Host "Pressione Enter para fechar"
    exit 0
}
catch {
    Fail 99 ("Erro inesperado: " + $_.Exception.Message)
}
