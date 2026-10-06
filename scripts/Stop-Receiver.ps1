$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskActive = Join-Path $taskRoot 'runs\active.json'
if (!(Test-Path -LiteralPath $taskActive)) { Write-Output '没有本项目接收记录。'; return }
$taskInfo = Get-Content -LiteralPath $taskActive -Raw | ConvertFrom-Json
$taskStop = [IO.Path]::GetFullPath($taskInfo.stopFile)
$taskRuns = [IO.Path]::GetFullPath((Join-Path $taskRoot 'runs')) + [IO.Path]::DirectorySeparatorChar
if (!$taskStop.StartsWith($taskRuns,[StringComparison]::OrdinalIgnoreCase)) { throw '停止文件不在本项目runs目录。' }
New-Item -ItemType File -Path $taskStop -Force | Out-Null
Write-Output '已请求电脑接收端停止；手表本地采集请在手表点击关闭心率广播。'
