#!/usr/bin/env pwsh
# Arranca el servidor de desarrollo en Windows.
# Uso:  .\start.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creando entorno virtual..." -ForegroundColor Cyan
    python -m venv $venv
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r (Join-Path $root "backend\requirements.txt")
}

Write-Host "Servidor en http://127.0.0.1:8000" -ForegroundColor Green
Set-Location (Join-Path $root "backend")
# --no-proxy-headers es importante: uvicorn lo trae activado por defecto y
# reescribe la IP del cliente con la cabecera X-Forwarded-For, que cualquiera
# puede inventarse. Con esto la IP es siempre la del par que abre la conexión,
# y el límite de peticiones no se puede saltar. Si algún día pones un proxy
# propio delante, se declara en VDL_TRUSTED_PROXIES y lo gestiona la aplicación.
& $python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
