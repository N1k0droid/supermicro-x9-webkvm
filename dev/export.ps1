# Copies only the files meant for publishing (GitHub) or for building the container
# (Synology Container Manager, docker compose...) into dist\supermicro-x9-webkvm.
# Secrets (.env, metrics token), local data, virtualenv and caches are never copied.
# Usage: powershell -ExecutionPolicy Bypass -File .\dev\export.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'dist\supermicro-x9-webkvm'

$files = @('.dockerignore', '.env.example', '.gitignore', 'Dockerfile', 'docker-compose.yml',
           'requirements.txt', 'README.md', 'README.it.md', 'LICENSE',
           'dev\run-local.ps1', 'dev\make-env.ps1', 'dev\export.ps1')
$dirs = @('docs', 'tests', 'webkvm', 'zabbix')

if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force $out | Out-Null
foreach ($f in $files) {
    $src = Join-Path $root $f
    if (-not (Test-Path $src)) { throw "missing file: $f" }
    $dst = Join-Path $out $f
    New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
    Copy-Item $src $dst
}
foreach ($d in $dirs) { Copy-Item (Join-Path $root $d) $out -Recurse }
Get-ChildItem $out -Recurse -Directory -Force |
    Where-Object { $_.Name -eq '__pycache__' -or $_.FullName -like '*\static\novnc' } |
    Remove-Item -Recurse -Force

# safety checks: nothing secret or personal may end up in the package
$all = Get-ChildItem $out -Recurse -File -Force
$bad = $all | Where-Object { $_.Name -in @('.env', 'metrics-token.txt') -or $_.Extension -in @('.pyc', '.sqlite') }
if ($bad) { throw "forbidden files in the package: $($bad.FullName -join ', ')" }
$tokenFile = Join-Path $root 'dev\metrics-token.txt'
if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    if ($token) {
        $hit = $all | Select-String -SimpleMatch -Pattern $token -List
        if ($hit) { throw "the metrics token appears in: $($hit.Path -join ', ')" }
    }
}

$all | ForEach-Object { $_.FullName.Substring($out.Length + 1) } | Sort-Object
Write-Host "Ready: $($all.Count) files in $out"
