[CmdletBinding()]
param(
    [ValidateRange(10,120)][int]$Seconds=30,
    [int]$ReceiverPid=0,
    [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference='Stop'
$measureRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$measureDashboard=Get-Content -LiteralPath (Join-Path $measureRoot 'runs\dashboard.json') -Raw | ConvertFrom-Json
$measureProcess=Get-Process -Id $measureDashboard.pid
if ($measureProcess.StartTime.ToUniversalTime().Ticks -ne ([DateTime]$measureDashboard.processStartUtc).ToUniversalTime().Ticks) {
    throw 'Dashboard process identity changed.'
}
$measureSystem=Get-CimInstance Win32_ComputerSystem
$measureCores=[int]$measureSystem.NumberOfLogicalProcessors
$measureQueue=[Collections.Generic.Queue[object]]::new()
$measureQueue.Enqueue(@{id=[int]$measureDashboard.pid;group='dashboard'})
if ($ReceiverPid -gt 0) { $measureQueue.Enqueue(@{id=$ReceiverPid;group='receiver'}) }
$measureTargets=@{}
while ($measureQueue.Count -gt 0) {
    $measureItem=$measureQueue.Dequeue()
    if ($measureTargets.ContainsKey($measureItem.id)) { continue }
    $measureProcess=Get-Process -Id $measureItem.id -ErrorAction Stop
    $measureGroup=if ($measureProcess.ProcessName -eq 'HeartRadioWidgets') {'desktop_widgets'} else {$measureItem.group}
    $measureTargets[$measureItem.id]=@{id=$measureItem.id;group=$measureGroup;name=$measureProcess.ProcessName;startTicks=$measureProcess.StartTime.ToUniversalTime().Ticks}
    foreach ($measureChild in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($measureItem.id)")) {
        $measureQueue.Enqueue(@{id=[int]$measureChild.ProcessId;group=$measureGroup})
    }
}
$measureRows=[Collections.Generic.List[object]]::new()
$measureStart=[DateTime]::UtcNow.ToString('o')
$measureClock=[Diagnostics.Stopwatch]::StartNew()
function Read-ResourceSnapshot {
    $measureValues=@()
    foreach ($measureTarget in $measureTargets.Values) {
        $measureCurrent=Get-Process -Id $measureTarget.id -ErrorAction SilentlyContinue
        if (!$measureCurrent -or $measureCurrent.StartTime.ToUniversalTime().Ticks -ne $measureTarget.startTicks) {
            throw "A measured process exited or changed identity: $($measureTarget.id)"
        }
        $measureValues+=@{pid=$measureCurrent.Id;group=$measureTarget.group;cpuSeconds=$measureCurrent.TotalProcessorTime.TotalSeconds;
            workingSetBytes=$measureCurrent.WorkingSet64;privateCommitBytes=$measureCurrent.PrivateMemorySize64}
    }
    return @{elapsedSeconds=$measureClock.Elapsed.TotalSeconds;processes=$measureValues}
}
$measureRows.Add((Read-ResourceSnapshot))
while ($measureClock.Elapsed.TotalSeconds -lt $Seconds) {
    Start-Sleep -Milliseconds 1000
    $measureRows.Add((Read-ResourceSnapshot))
}
$measureElapsed=$measureRows[-1].elapsedSeconds-$measureRows[0].elapsedSeconds
$measureSummary=@()
foreach ($measureGroup in @('dashboard','desktop_widgets','receiver','total')) {
    $measureSeries=@($measureRows | ForEach-Object {
        $measureSelected=@($_.processes | Where-Object { $measureGroup -eq 'total' -or $_.group -eq $measureGroup })
        [pscustomobject]@{time=$_.elapsedSeconds;cpu=($measureSelected | Measure-Object cpuSeconds -Sum).Sum;
            ws=($measureSelected | Measure-Object workingSetBytes -Sum).Sum;
            commit=($measureSelected | Measure-Object privateCommitBytes -Sum).Sum;count=$measureSelected.Count}
    })
    if (!$measureSeries[0].count) { continue }
    $measurePeaks=@(for($measureIndex=1;$measureIndex -lt $measureSeries.Count;$measureIndex++) {
        100*($measureSeries[$measureIndex].cpu-$measureSeries[$measureIndex-1].cpu)/($measureSeries[$measureIndex].time-$measureSeries[$measureIndex-1].time)/$measureCores
    })
    $measureWs=$measureSeries | Measure-Object ws -Average -Minimum -Maximum
    $measureCommit=$measureSeries | Measure-Object commit -Average -Maximum
    $measureSummary+=[ordered]@{group=$measureGroup;processCount=$measureSeries[0].count;
        cpuPercentAverage=[Math]::Round(100*($measureSeries[-1].cpu-$measureSeries[0].cpu)/$measureElapsed/$measureCores,4);
        cpuPercentMaxSample=[Math]::Round(($measurePeaks | Measure-Object -Maximum).Maximum,4);
        workingSetMiBAverage=[Math]::Round($measureWs.Average/1MB,2);workingSetMiBMin=[Math]::Round($measureWs.Minimum/1MB,2);workingSetMiBMax=[Math]::Round($measureWs.Maximum/1MB,2);
        privateCommitMiBAverage=[Math]::Round($measureCommit.Average/1MB,2)}
}
$measureResult=[ordered]@{startedUtc=$measureStart;durationSeconds=$measureElapsed;logicalProcessors=$measureCores;physicalMemoryGiB=[Math]::Round($measureSystem.TotalPhysicalMemory/1GB,2);
    cpuDenominator='all logical processors; 100% means the entire machine';
    memoryNote='Working sets summed by process; shared pages may be counted twice. Private commit is not physical RAM.';
    exclusions=@('browser/Codex shared processes','GPU','watch hardware');
    processes=@($measureTargets.Values);summary=$measureSummary;samples=@($measureRows.ToArray())}
$measureFullOutput=[IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($measureFullOutput)) | Out-Null
[IO.File]::WriteAllText($measureFullOutput,($measureResult | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
$measureSummary | ConvertTo-Json -Depth 4
