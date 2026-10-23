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
    try {
        $taskVersion=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/version' -TimeoutSec 2
        $taskOwnedApi=$taskVersion.pid -eq $taskExisting.Id
        if (!$taskOwnedApi -and $taskVersion.pid) {
            $taskApiProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$taskVersion.pid)" -ErrorAction Stop
            $taskOwnedApi=$taskApiProcess.ParentProcessId -eq $taskExisting.Id -and $taskApiProcess.CommandLine -like "*$taskRoot\dashboard\server.py*"
        }
        if ($taskOwnedApi) {
            $taskBody=@{action='stop'}|ConvertTo-Json -Compress
            Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8766/api/dashboard-stop' -Method Post -ContentType 'application/json' -Headers @{'X-HeartRadio-Action'='dashboard-stop'} -Body $taskBody -TimeoutSec 40 | Out-Null
            $taskExisting.WaitForExit(5000)|Out-Null
        }
    } catch { Write-Output '仪表盘未完成正常退出，继续按已核对的项目进程身份停止。' }
    if ($taskExisting.HasExited) { Write-Output '已正常关闭本项目仪表盘与其连接管理。';return }
    Stop-Process -Id $taskExisting.Id -ErrorAction Stop
    Write-Output '已停止本项目仪表盘服务；接收器和手表采集未受此操作影响。'
}
