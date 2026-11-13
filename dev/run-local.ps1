# Runs the gateway on this Windows PC, reachable from this PC only (http://127.0.0.1:8080).
# Usage (from the project folder, with a .venv that has requirements.txt installed):
#   powershell -ExecutionPolicy Bypass -File .\dev\run-local.ps1
# Uses BMC_HOST / BMC_USER / BMC_PASSWORD if already set in the terminal, otherwise asks.
# Passwords are never shown on screen. Ctrl+C to stop.
# The KVM page also needs noVNC: put its core/ and vendor/ folders in webkvm\web\static\novnc
# (or set NOVNC_DIR).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Read-Secret($prompt) {
    $s = Read-Host $prompt -AsSecureString
    return [Net.NetworkCredential]::new('', $s).Password
}

if (-not $env:BMC_HOST) { $env:BMC_HOST = Read-Host 'BMC address' }
if (-not $env:BMC_USER) { $env:BMC_USER = Read-Host 'BMC user' }
if (-not $env:BMC_PASSWORD) { $env:BMC_PASSWORD = Read-Secret 'BMC password' }

Write-Host 'Choose a password to sign in to the gateway (user: admin)'
$env:WEBKVM_USER = 'admin'
$env:WEBKVM_PASSWORD = Read-Secret 'Gateway password'

$env:WEBKVM_LISTEN = '127.0.0.1'      # this PC only
$env:WEBKVM_PORT = '8080'
$env:VNC_ENABLE = 'false'             # no native VNC port for local tests
$env:DATA_DIR = Join-Path $root 'data-local'
$env:LOG_LEVEL = 'INFO'

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "No .venv: create it with  py -m venv .venv  and install requirements.txt" }
Write-Host "Gateway on http://127.0.0.1:8080 (BMC $($env:BMC_HOST)). Ctrl+C to stop."
& $py -m webkvm
