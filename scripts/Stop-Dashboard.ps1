[CmdletBinding(SupportsShouldProcess)]
param()
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskState=Join-Path $taskRoot 'runs\dashboard.json'
if (!(Test-Path -LiteralPath $taskState)) { Write-Output '没有本项目仪表盘进程记录。'; return }
$taskInfo=Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
$taskExisting=Get-Process -Id $taskInfo.pid -ErrorAction SilentlyContinue
if (!$taskExisting) { Write-Output '本项目仪表盘已经停止。'; return }
if ($taskExisting.StartTime.ToUniversalTime().Ticks -ne ([DateTime]$taskInfo.processStartUtc).ToUniversalTime().Ticks) {
    throw '进程身份与本项目记录不符，未停止任何进程。'
}
if ($PSCmdlet.ShouldProcess("本项目仪表盘 PID $($taskExisting.Id)",'停止')) {
    Stop-Process -Id $taskExisting.Id -ErrorAction Stop
    Write-Output '已停止本项目仪表盘服务；接收器和手表采集未受此操作影响。'
}
