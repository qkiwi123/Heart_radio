[CmdletBinding()]
param([ValidateSet('start','start-if-enabled','stop','status')][string]$Action='status')
$ErrorActionPreference='Stop'
$serviceRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$servicePython=Join-Path $serviceRoot '.venv\Scripts\python.exe'
$serviceEncoding=$env:PYTHONIOENCODING
$serviceConsoleEncoding=[Console]::OutputEncoding
try {
    $env:PYTHONIOENCODING='utf-8'
    [Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
    $serviceRaw=& $servicePython (Join-Path $PSScriptRoot 'capability_service.py') $Action
    if ($LASTEXITCODE -ne 0) { throw (($serviceRaw | ConvertFrom-Json).error) }
    $serviceResult=$serviceRaw | ConvertFrom-Json
} finally { $env:PYTHONIOENCODING=$serviceEncoding; [Console]::OutputEncoding=$serviceConsoleEncoding }
switch ($serviceResult.action) {
    'disabled' { Write-Output '按需服务未设为随01启动；保留仅心率方式，可在网页开启。' }
    'started' {
        if ($serviceResult.durationSeconds -eq 0) { Write-Output '电脑按需服务已就绪，本次不限时；请在手表开启待命通道，可选择跟随电脑设置。' }
        else { Write-Output "电脑按需服务已就绪，本次$($serviceResult.durationSeconds/60)分钟；请在手表开启待命通道。" }
    }
    'reused' { Write-Output '已复用电脑按需服务，原期限不重新计时。' }
    'stopped' {
        Write-Output '电脑按需服务已停止，已有数据保留。'
        if ($serviceResult.watchStopStatus -eq 'stop_sent') { Write-Output '已向手表发送关闭待命请求；心率广播独立运行。' }
        else { Write-Warning '未能确认手表收到关闭待命请求；请在手表关闭。不限时没有自动到期。' }
    }
    'already_stopped' {
        if ($serviceResult.state -eq 'port_conflict') { Write-Warning '发现未由日常入口管理的接口服务；未操作该进程，请按其原启动方式停止。' }
        else { Write-Output '电脑按需服务未运行。若手表仍在离线待命，请在手表关闭。' }
    }
    default { $serviceResult | ConvertTo-Json -Depth 6 }
}
