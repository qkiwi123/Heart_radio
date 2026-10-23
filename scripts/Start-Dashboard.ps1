[CmdletBinding()]
param([switch]$OpenBrowser,[switch]$EnsureCurrent)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskState=Join-Path $taskRoot 'runs\dashboard.json'
$taskGuard=[Threading.Mutex]::new($false,'Local\HeartRadioDashboardLauncher')
if (!$taskGuard.WaitOne(0)) { throw '仪表盘正在启动。' }
try {
    $taskRunning=$false
    if (Test-Path -LiteralPath $taskState) {
        $taskInfo=Get-Content -LiteralPath $taskState -Raw | ConvertFrom-Json
        $taskExisting=Get-Process -Id $taskInfo.pid -ErrorAction SilentlyContinue
        $taskRunning=$taskExisting -and $taskExisting.StartTime.ToUniversalTime().Ticks -eq ([DateTime]$taskInfo.processStartUtc).ToUniversalTime().Ticks
    }
    if ($taskRunning -and $EnsureCurrent) {
        $taskExpected=& (Join-Path $taskRoot '.venv\Scripts\python.exe') (Join-Path $taskRoot 'dashboard\build_identity.py')
        if ($LASTEXITCODE -ne 0 -or $taskExpected -notmatch '^[0-9a-f]{64}$') { throw '无法核对电脑服务源码版本，未停止现有服务。' }
        $taskVersion=$null
        try { $taskVersion=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/version' -TimeoutSec 2 } catch { }
        if (!$taskVersion -or $taskVersion.serverBuild -ne $taskExpected) {
            & "$PSScriptRoot\Stop-Dashboard.ps1" -Confirm:$false
            $taskRunning=$false
        }
    }
    if (!$taskRunning) {
        if (Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue) { throw '8766端口已有其他程序，未结束它。' }
        New-Item -ItemType Directory -Path (Join-Path $taskRoot 'runs') -Force | Out-Null
        $taskArgs=@('-u',('"'+(Join-Path $taskRoot 'dashboard\server.py')+'"'))
        $taskProc=Start-Process -FilePath (Join-Path $taskRoot '.venv\Scripts\python.exe') -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $taskRoot 'runs\dashboard.stdout.txt') -RedirectStandardError (Join-Path $taskRoot 'runs\dashboard.stderr.txt')
        $taskDeadline=[DateTime]::UtcNow.AddSeconds(8)
        $taskReady=$false
        while ([DateTime]::UtcNow -lt $taskDeadline) {
            if ($taskProc.HasExited) { throw '仪表盘启动失败，请查看runs/dashboard.stderr.txt。' }
            try { $taskResponse=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8766/api/state' -TimeoutSec 1; $taskReady=$taskResponse.StatusCode -eq 200 } catch { }
            if ($taskReady) { break }; Start-Sleep -Milliseconds 200
        }
        if (!$taskReady) { throw '仪表盘尚未就绪，请查看日志。' }
        [IO.File]::WriteAllText($taskState,(@{pid=$taskProc.Id;processStartUtc=$taskProc.StartTime.ToUniversalTime().ToString('o');url='http://127.0.0.1:8766'}|ConvertTo-Json))
    }
    if ($OpenBrowser) { Start-Process 'http://127.0.0.1:8766' }
    Write-Output '仪表盘：http://127.0.0.1:8766；关闭网页不停止接收。'
} finally { $taskGuard.ReleaseMutex();$taskGuard.Dispose() }
