[CmdletBinding()]
param([string]$WatchAddress = '', [ValidateRange(0,525600)][int]$Minutes)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskActive = Join-Path $taskRoot 'runs\active.json'
$taskGuard = [Threading.Mutex]::new($false, 'Local\HeartRadioReceiverLauncher')
if (!$taskGuard.WaitOne(0)) { throw '另一个接收端启动操作正在进行。' }
try {
    if (Test-Path -LiteralPath $taskActive) {
        $taskOld = Get-Content -LiteralPath $taskActive -Raw | ConvertFrom-Json
        $taskExisting = Get-Process -Id $taskOld.pid -ErrorAction SilentlyContinue
        if ($taskExisting -and $taskExisting.StartTime.ToUniversalTime().Ticks -eq ([DateTime]$taskOld.processStartUtc).ToUniversalTime().Ticks) {
            Write-Output "接收端已运行，复用本轮记录：$($taskOld.runId)"
            return
        }
    }
    if ($WatchAddress -and $WatchAddress -notmatch '^(\d{1,3}\.){3}\d{1,3}$') { throw '请指定已确认的手表IPv4地址。' }
    $taskConfigDir = Join-Path $taskRoot '.local-wifi'
    $taskConfig = Get-Content -LiteralPath (Join-Path $taskConfigDir 'config.json') -Raw | ConvertFrom-Json
    if (Get-NetTCPConnection -State Listen -LocalPort $taskConfig.port -ErrorAction SilentlyContinue) { throw '接收端口已被其他进程占用，未结束或覆盖它。' }
    $taskId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $taskRun = Join-Path $taskRoot "runs\$taskId"
    $taskFrozen = Join-Path $taskRun 'receiver'
    New-Item -ItemType Directory -Path $taskFrozen -Force | Out-Null
    foreach ($taskName in @('wifi_receive.py','protocol.py','control.py','discovery.py','lan.py')) {
        Copy-Item -LiteralPath (Join-Path $taskRoot "receiver\$taskName") -Destination (Join-Path $taskFrozen $taskName)
    }
    $taskOutput = Join-Path $taskRun 'pc.jsonl'
    $taskStop = Join-Path $taskRun 'stop.request'
    $taskSettings = Join-Path $taskRoot 'runs\receiver-settings.json'
    $taskSeconds = 0
    if (Test-Path -LiteralPath $taskSettings) { $taskSeconds=(Get-Content -LiteralPath $taskSettings -Raw | ConvertFrom-Json).seconds }
    $taskArgs = @('-u', ('"'+(Join-Path $taskFrozen 'wifi_receive.py')+'"'), '--directory', ('"'+$taskConfigDir+'"'),
                  '--follow-lan','--discovery','--settings',('"'+$taskSettings+'"'),
                  '--output', ('"'+$taskOutput+'"'), '--stop-file', ('"'+$taskStop+'"'))
    if ($WatchAddress) { $taskArgs += @('--allow-watch',$WatchAddress) }
    if ($PSBoundParameters.ContainsKey('Minutes')) { $taskSeconds=$Minutes*60; $taskArgs += @('--seconds',$taskSeconds) }
    # Hash before launching. Use .NET so inherited module paths cannot leave an unregistered child.
    $taskHashEngine=[Security.Cryptography.SHA256]::Create()
    $taskSourceStream=[IO.File]::OpenRead((Join-Path $taskFrozen 'wifi_receive.py'))
    try { $taskSourceHash=[BitConverter]::ToString($taskHashEngine.ComputeHash($taskSourceStream)).Replace('-','') }
    finally { $taskSourceStream.Dispose(); $taskHashEngine.Dispose() }
    $taskProc = Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $taskRun 'receiver.stdout.txt') -RedirectStandardError (Join-Path $taskRun 'receiver.stderr.txt')
    $taskMetadata = [ordered]@{runId=$taskId;pid=$taskProc.Id;processStartUtc=$taskProc.StartTime.ToUniversalTime().ToString('o');
        startedUtc=[DateTime]::UtcNow.ToString('o');durationSeconds=$taskSeconds;host=$taskConfig.host;port=$taskConfig.port;watchAddress=$WatchAddress;
        logPath=$taskOutput;stopFile=$taskStop;runDirectory=$taskRun;receiverSha256=$taskSourceHash}
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(10)
    while ([DateTime]::UtcNow -lt $taskDeadline) {
        if ($taskProc.HasExited) { throw "接收端启动失败，查看 $taskRun\receiver.stderr.txt" }
        if ((Test-Path -LiteralPath $taskOutput) -and (Select-String -LiteralPath $taskOutput -Pattern '"server_start"' -Quiet)) { break }
        Start-Sleep -Milliseconds 200
    }
    if (!(Test-Path -LiteralPath $taskOutput) -or !(Select-String -LiteralPath $taskOutput -Pattern '"server_start"' -Quiet)) {
        New-Item -ItemType File -Path $taskStop -Force | Out-Null
        throw '接收端未在10秒内就绪，已请求停止。'
    }
    $taskJson = $taskMetadata | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $taskRun 'run.json'),$taskJson)
    [IO.File]::WriteAllText($taskActive,$taskJson)
    $taskLimitLabel=if ($taskSeconds -eq 0) {'不限时'} else {"$($taskSeconds/60) 分钟"}
    Write-Output "接收端已就绪：${taskId}；接收时长：${taskLimitLabel}（仪表盘可修改）；日志：$taskOutput"
} finally { $taskGuard.ReleaseMutex(); $taskGuard.Dispose() }
