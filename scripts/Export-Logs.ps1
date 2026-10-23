[CmdletBinding()]
param([string]$Serial, [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\evidence\device-logs'),
      [ValidatePattern('^session-[0-9-]+\.jsonl$')][string]$Session)
$ErrorActionPreference = 'Stop'
$watch = & "$PSScriptRoot\Get-Watch.ps1" -Serial $Serial
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$taskCopies=@()
foreach($taskDirectory in @('logs','rfcomm-probe','capability-probe')) {
    if($Session -and $taskDirectory -ne 'logs'){continue}
    $names = & $watch.Adb -s $watch.Serial shell run-as $watch.Package ls ("files/"+$taskDirectory) 2>$null
    if ($LASTEXITCODE -ne 0) {
        if($taskDirectory -eq 'logs'){throw '无法读取本 App 私有日志。请先运行 debug App；没有使用 root。'}
        continue
    }
    foreach($taskLine in $names){
        $name=$taskLine.Trim()
        $taskAllowed=if($taskDirectory -eq 'logs'){'^(session-[0-9-]+|lifecycle)\.jsonl(\.part-[0-9]+-[0-9]+\.jsonl)?$'}elseif($taskDirectory -eq 'rfcomm-probe'){'^events\.jsonl(\.part-[0-9]+-[0-9]+\.jsonl)?$'}else{'^(requests|bridge-events)\.jsonl(\.part-[0-9]+-[0-9]+\.jsonl)?$'}
        if($name -notmatch $taskAllowed){continue}
        if($Session -and $name -ne $Session -and !$name.StartsWith($Session+'.part-')){continue}
        $taskCopies+=@{directory=$taskDirectory;name=$name}
    }
}
if($Session -and !$taskCopies.Count){throw "本项目日志中没有指定会话或保留分段：$Session"}
foreach ($taskCopy in $taskCopies) {
    $name=$taskCopy.name
    $taskDestination=Join-Path (Join-Path $OutputDirectory 'segments') $taskCopy.directory
    New-Item -ItemType Directory -Force $taskDestination | Out-Null
    # Copy raw UTF-8 bytes; PowerShell/native-console decoding can corrupt Chinese log fields.
    $taskInfo = [Diagnostics.ProcessStartInfo]::new()
    $taskInfo.FileName = $watch.Adb
    $taskInfo.Arguments = "-s $($watch.Serial) exec-out run-as $($watch.Package) cat files/$($taskCopy.directory)/$name"
    $taskInfo.UseShellExecute = $false
    $taskInfo.CreateNoWindow = $true
    $taskInfo.RedirectStandardOutput = $true
    $taskInfo.RedirectStandardError = $true
    $taskProcess = [Diagnostics.Process]::Start($taskInfo)
    $taskStream = [IO.File]::Open((Join-Path $taskDestination $name),[IO.FileMode]::Create,[IO.FileAccess]::Write)
    try { $taskProcess.StandardOutput.BaseStream.CopyTo($taskStream) } finally { $taskStream.Dispose() }
    $taskError = $taskProcess.StandardError.ReadToEnd()
    $taskProcess.WaitForExit()
    if ($taskProcess.ExitCode -ne 0) { throw "导出失败：${name} $taskError" }
    $taskProcess.Dispose()
}
& "$PSScriptRoot\Merge-DiagnosticExports.ps1" -OutputDirectory $OutputDirectory
Write-Output "已导出到 $OutputDirectory，segments保留原分段，另生成兼容合并副本（活动末行可能尚未完整，请停止后再导出）"
