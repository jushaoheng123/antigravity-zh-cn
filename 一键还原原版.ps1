$ErrorActionPreference = 'Stop'

$BaseDir  = $PSScriptRoot
$Target   = Join-Path $env:LOCALAPPDATA 'Programs\Antigravity\resources\app.asar'
if (-not (Test-Path $Target)) {
    $Target = Join-Path $env:LOCALAPPDATA 'Programs\antigravity\resources\app.asar'
}
$Manifest = Join-Path $BaseDir 'build\manifest.json'
$OrigBk   = Join-Path $BaseDir 'backup\original\app.asar'

if (Test-Path $Manifest) {
    $mf = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($mf.source_backup -and (Test-Path ([string]$mf.source_backup))) {
        $OrigBk = [string]$mf.source_backup
    }
}

if (-not (Test-Path $OrigBk)) {
    Write-Host '[错误] 未找到原版备份:' $OrigBk -ForegroundColor Red
    exit 1
}

$procs = Get-Process -Name 'Antigravity' -ErrorAction SilentlyContinue
if ($procs) {
    Write-Host '[提示] 检测到 Antigravity 正在运行，正在关闭它以便还原 app.asar...' -ForegroundColor Yellow
    $procs | Stop-Process -Force
    Start-Sleep -Milliseconds 1200
}

Copy-Item -LiteralPath $OrigBk -Destination $Target -Force

$h1 = (Get-FileHash -LiteralPath $OrigBk -Algorithm SHA256).Hash
$h2 = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash
if ($h1 -ne $h2) {
    Write-Host '[错误] 校验不一致！' -ForegroundColor Red
    exit 1
}

Write-Host '[完成] 已还原为官方原版 Antigravity！备份源:' $OrigBk -ForegroundColor Green
Write-Host 'SHA256:' $h2 -ForegroundColor Cyan
