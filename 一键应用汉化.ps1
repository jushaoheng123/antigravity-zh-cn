$ErrorActionPreference = 'Stop'

$BaseDir  = $PSScriptRoot
$Target   = Join-Path $env:LOCALAPPDATA 'Programs\Antigravity\resources\app.asar'
if (-not (Test-Path $Target)) {
    $Target = Join-Path $env:LOCALAPPDATA 'Programs\antigravity\resources\app.asar'
}
$Built    = Join-Path $BaseDir 'build\app.asar'
$Manifest = Join-Path $BaseDir 'build\manifest.json'
$PatchPy  = Join-Path $BaseDir 'patch-app.py'

if (-not (Test-Path $Target)) {
    Write-Host '[错误] 未找到 Antigravity app.asar，可通过 $env:AGY_ASAR_PATH 指定路径:' $Target -ForegroundColor Red
    exit 1
}

$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) { $pyCmd = Get-Command py -ErrorAction SilentlyContinue }

$liveHash = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant()
$needRebuild = $false

if (-not (Test-Path $Built) -or -not (Test-Path $Manifest)) {
    Write-Host '[检测] 尚无构建记录，准备现场解析并构建汉化补丁...' -ForegroundColor Yellow
    $needRebuild = $true
} else {
    $mf = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    $patchedSha = [string]$mf.patched_sha256
    $sourceSha  = [string]$mf.source_sha256
    if ($liveHash -eq $patchedSha.ToLowerInvariant()) {
        Write-Host "[检测] 当前已经是最新汉化版 (v$($mf.source_version))。" -ForegroundColor Cyan
    } elseif ($liveHash -eq $sourceSha.ToLowerInvariant()) {
        Write-Host "[检测] 当前为原版 (v$($mf.source_version))，直接应用已构建补丁。" -ForegroundColor Cyan
    } else {
        Write-Host '[更新检测] 检测到 Antigravity 已更新（或版本变化）！正在基于新版重新打包汉化补丁...' -ForegroundColor Magenta
        $needRebuild = $true
    }
}

if ($needRebuild) {
    if (-not $pyCmd) {
        Write-Host '[错误] 未找到 Python，请先安装 Python 3.10+ 并加入 PATH。' -ForegroundColor Red
        exit 1
    }
    $env:AGY_ASAR_PATH = $Target
    & $pyCmd.Source $PatchPy
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[错误] 重新构建补丁失败！原文件未被修改。' -ForegroundColor Red
        exit 1
    }
}

$procs = Get-Process -Name 'Antigravity' -ErrorAction SilentlyContinue
if ($procs) {
    Write-Host '[提示] 检测到 Antigravity 正在运行，正在关闭它以便替换 app.asar...' -ForegroundColor Yellow
    $procs | Stop-Process -Force
    Start-Sleep -Milliseconds 1200
}

Copy-Item -LiteralPath $Built -Destination $Target -Force

$h1 = (Get-FileHash -LiteralPath $Built -Algorithm SHA256).Hash
$h2 = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash
if ($h1 -ne $h2) {
    Write-Host '[错误] 校验不一致，替换可能未生效！' -ForegroundColor Red
    exit 1
}

Write-Host '[完成] Antigravity 中文汉化已应用！SHA256:' $h2 -ForegroundColor Green
Write-Host '现在可以重新打开 Antigravity 啦~' -ForegroundColor Cyan
