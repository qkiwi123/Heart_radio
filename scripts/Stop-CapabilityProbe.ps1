[CmdletBinding()]
param([switch]$KeepWatchStandby)
$ErrorActionPreference = 'Stop'
$probeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$probeActive = Join-Path $probeRoot 'runs\capability-probe-active.json'
if (!(Test-Path -LiteralPath $probeActive)) { throw '没有由启动脚本记录的接口测试。' }
$probeMetadata = Get-Content -LiteralPath $probeActive -Raw | ConvertFrom-Json
$probeAllowed = [IO.Path]::GetFullPath((Join-Path $probeRoot 'evidence\capability-probe')) + [IO.Path]::DirectorySeparatorChar
$probeStop = [IO.Path]::GetFullPath($probeMetadata.stopFile)
if (!$probeStop.StartsWith($probeAllowed, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($probeStop) -ne 'stop.request') {
    throw '停止文件路径不属于本项目接口测试目录。'
}
$probeProcess = Get-Process -Id $probeMetadata.pid -ErrorAction SilentlyContinue
if (!$probeProcess -or $probeProcess.StartTime.ToUniversalTime().Ticks -ne ([DateTime]$probeMetadata.processStartUtc).ToUniversalTime().Ticks) {
    Write-Output '该轮接口测试进程已结束。'; return
}
[IO.File]::WriteAllText($probeStop, $(if ($KeepWatchStandby) {'keep_watch_standby'} else {''}), [Text.UTF8Encoding]::new($false))
if ($KeepWatchStandby) { Write-Output '仅结束电脑桥接，保留手表本次待命。电脑恢复同一地址服务后手表可自动重连；待命期限不延长。' }
else { Write-Output '已请求结束电脑接口测试；电脑会向已连接的手表发送停止待命。若手表离线，需在手表停止或等待原30分钟期限结束。原心率服务独立。' }
