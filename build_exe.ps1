# TgPlayer v6.4.15 - Build robusto do .exe (Windows)
# Execute: powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$Log = Join-Path $PSScriptRoot "build_log.txt"
Start-Transcript -Path $Log -Force | Out-Null

function Finish($code) {
    try { Stop-Transcript | Out-Null } catch {}
    Write-Host ""
    if ($code -eq 0) {
        Write-Host "[SUCESSO] dist\TgPlayer\TgPlayer.exe gerado." -ForegroundColor Green
        Write-Host "Envie ao usuario final a pasta inteira: dist\TgPlayer" -ForegroundColor Green
    } else {
        Write-Host "[ERRO] Build falhou. Veja build_log.txt" -ForegroundColor Red
        Write-Host "Dica: use Python 3.11 ou 3.12 de 64 bits. Evite Python 3.13." -ForegroundColor Yellow
    }
    exit $code
}

function Run-Step($title, $scriptBlock) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host $title -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
    & $scriptBlock
}

try {
    Write-Host "TgPlayer v6.4.15 - Build do executavel" -ForegroundColor Cyan
    Write-Host "Log: $Log" -ForegroundColor Gray
    Write-Host "A primeira execucao pode demorar 5 a 15 minutos." -ForegroundColor Yellow

    Run-Step "[1/6] Localizando Python 3.10/3.11/3.12" {
        $candidates = @("py -3.12", "py -3.11", "py -3.10", "python")
        $script:pycmd = $null
        foreach ($cmd in $candidates) {
            cmd /c "$cmd -c ""import sys; raise SystemExit(0 if sys.version_info[:2] in [(3,10),(3,11),(3,12)] and sys.maxsize > 2**32 else 1)""" | Out-Null
            if ($LASTEXITCODE -eq 0) { $script:pycmd = $cmd; break }
        }
        if (-not $script:pycmd) { throw "Python 3.10/3.11/3.12 de 64 bits nao encontrado." }
        Write-Host "Usando: $script:pycmd"
        cmd /c "$script:pycmd -c ""import sys, platform; print(sys.version); print(platform.architecture())"""
    }

    Run-Step "[2/6] Preparando ambiente virtual .venv-build" {
        if (-not (Test-Path ".venv-build\Scripts\python.exe")) {
            cmd /c "$script:pycmd -m venv .venv-build"
            if ($LASTEXITCODE -ne 0) { throw "Falha ao criar .venv-build." }
        } else {
            Write-Host "Reutilizando .venv-build existente para acelerar."
        }
        & ".\.venv-build\Scripts\Activate.ps1"
        python -c "import sys; print('Venv:', sys.executable)"
    }

    Run-Step "[3/6] Atualizando pip/setuptools/wheel" {
        python -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
        if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar pip." }
    }

    Run-Step "[4/6] Instalando dependencias" {
        python -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar requirements.txt." }
    }

    Run-Step "[5/6] Instalando/atualizando PyInstaller" {
        python -m pip install --disable-pip-version-check --upgrade "pyinstaller>=6.3"
        if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar PyInstaller." }
    }

    Run-Step "[6/6] Gerando executavel" {
        if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
        if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
        python -m PyInstaller --clean --noconfirm TgPlayer.spec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller retornou erro." }
        if (-not (Test-Path "dist\TgPlayer\TgPlayer.exe")) { throw "TgPlayer.exe nao foi encontrado em dist\TgPlayer." }
    }

    Finish 0
} catch {
    Write-Host ""
    Write-Host $_.Exception.Message -ForegroundColor Red
    Finish 1
}
