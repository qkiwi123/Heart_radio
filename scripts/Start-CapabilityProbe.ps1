[CmdletBinding()]
param([string]$Serial, [string]$Python = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'),
      [ValidateRange(1,1800)][int]$Seconds = 1800, [switch]$ManualWatchStart, [switch]$ComputerOnly)
$ErrorActionPreference = 'Stop'
$probeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (Get-NetTCPConnection -State Listen -LocalPort 8767,8768 -ErrorAction SilentlyContinue) {
    throw '接口测试端口已占用；请先停止已有接口测试，不会结束其他进程。'
}
$probeDirectory = Join-Path $probeRoot ('evidence\capability-probe\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $probeDirectory | Out-Null
if (!$ComputerOnly) {
    & $Python (Join-Path $probeRoot 'scripts\capability_probe.py') provision --serial $Serial
    if ($LASTEXITCODE -ne 0) { throw '本应用私有测试配置写入失败。' }
}
$probeStop = Join-Path $probeDirectory 'stop.request'
$probeArguments = @('-u',('"'+(Join-Path $probeRoot 'receiver\capability_bridge.py')+'"'),
    '--directory',('"'+(Join-Path $probeRoot '.local-wifi')+'"'), '--seconds', $Seconds,
    '--output',('"'+(Join-Path $probeDirectory 'pc-bridge.jsonl')+'"'), '--stop-file',('"'+$probeStop+'"'))
$probeProcess = Start-Process -FilePath $Python -ArgumentList $probeArguments -WorkingDirectory $probeRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $probeDirectory 'bridge.stdout.txt') -RedirectStandardError (Join-Path $probeDirectory 'bridge.stderr.txt')
try {
    $probeUntil = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $probeUntil) {
        if ($probeProcess.HasExited) { throw '接口测试服务启动失败，请查看本轮 bridge.stderr.txt。' }
        if (Select-String -LiteralPath (Join-Path $probeDirectory 'bridge.stdout.txt') -Pattern '"ready"' -Quiet -ErrorAction SilentlyContinue) { break }
        Start-Sleep -Milliseconds 200
    }
    if (!(Select-String -LiteralPath (Join-Path $probeDirectory 'bridge.stdout.txt') -Pattern '"ready"' -Quiet)) { throw '接口测试服务未就绪。' }
    $probeMetadata = [ordered]@{pid=$probeProcess.Id;processStartUtc=$probeProcess.StartTime.ToUniversalTime().ToString('o');
        runDirectory=$probeDirectory;stopFile=$probeStop;apiPort=8768;tlsPort=8767;seconds=$Seconds}
    $probeMetadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $probeRoot 'runs\capability-probe-active.json') -Encoding utf8
    $probeWatchArguments = @((Join-Path $probeRoot 'scripts\capability_probe.py'), 'start', '--serial', $Serial)
    if ($ManualWatchStart) { $probeWatchArguments += '--manual-start' }
    if (!$ComputerOnly) {
        & $Python @probeWatchArguments
        if ($LASTEXITCODE -ne 0) { throw '手表接口测试启动失败。' }
    }
    Write-Output "电脑接口测试已启动，最长 $Seconds 秒；目录：$probeDirectory"
    if ($ManualWatchStart) { Write-Output '手表尚未连接测试桥接；按提示手动点击“开启待命通道”。' }
    if ($ComputerOnly) { Write-Output '仅恢复电脑桥接；已开启的手表待命可自动重连，原待命期限不延长。' }
} catch {
    New-Item -ItemType File -Path $probeStop -Force | Out-Null
    throw
}
