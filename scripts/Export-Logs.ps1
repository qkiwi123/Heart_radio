[CmdletBinding()]
param([string]$Serial, [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\evidence\device-logs'),
      [ValidatePattern('^session-[0-9-]+\.jsonl$')][string]$Session)
$ErrorActionPreference = 'Stop'
$watch = & "$PSScriptRoot\Get-Watch.ps1" -Serial $Serial
New-Item -ItemType Directory -Force $OutputDirectory | Out-Null
$names = & $watch.Adb -s $watch.Serial shell run-as $watch.Package ls files/logs
if ($LASTEXITCODE -ne 0) { throw '无法读取本 App 私有日志。请先运行 debug App；没有使用 root。' }
if ($Session -and $Session -notin @($names | ForEach-Object { $_.Trim() })) { throw "本项目日志中没有指定文件：$Session" }
foreach ($name in $names) {
    $name = $name.Trim()
    if ($name -notmatch '^(session-[0-9-]+|lifecycle)\.jsonl$') { continue }
    if ($Session -and $name -ne $Session) { continue }
    # Copy raw UTF-8 bytes; PowerShell/native-console decoding can corrupt Chinese log fields.
    $taskInfo = [Diagnostics.ProcessStartInfo]::new()
    $taskInfo.FileName = $watch.Adb
    $taskInfo.Arguments = "-s $($watch.Serial) exec-out run-as $($watch.Package) cat files/logs/$name"
    $taskInfo.UseShellExecute = $false
    $taskInfo.CreateNoWindow = $true
    $taskInfo.RedirectStandardOutput = $true
    $taskInfo.RedirectStandardError = $true
    $taskProcess = [Diagnostics.Process]::Start($taskInfo)
    $taskStream = [IO.File]::Open((Join-Path $OutputDirectory $name),[IO.FileMode]::Create,[IO.FileAccess]::Write)
    try { $taskProcess.StandardOutput.BaseStream.CopyTo($taskStream) } finally { $taskStream.Dispose() }
    $taskError = $taskProcess.StandardError.ReadToEnd()
    $taskProcess.WaitForExit()
    if ($taskProcess.ExitCode -ne 0) { throw "导出失败：${name} $taskError" }
    $taskProcess.Dispose()
}
Write-Output "已导出本 App JSONL 到 $OutputDirectory（活动文件末行可能尚未写完，请停止后再次导出）"
