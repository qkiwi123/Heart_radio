[CmdletBinding()]
param([string]$Serial, [string]$ConnectionCodeFile = "$PSScriptRoot\..\.local-wifi\connection-code.txt")
$ErrorActionPreference = 'Stop'
$watch = & "$PSScriptRoot\Get-Watch.ps1" -Serial $Serial
$taskCode = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $ConnectionCodeFile).Path).Trim()
if ($taskCode -notmatch '^\d{1,3}(\.\d{1,3}){3}:\d{4,5}\|[0-9a-f]{64}\|[0-9a-f]{64}$') { throw '连接码格式错误。' }
# Quote for the device shell. Validated alphabet excludes shell metacharacters except pipes.
# Private service, own UID; this starts capture but never grants BODY_SENSORS permission.
& $watch.Adb -s $watch.Serial shell run-as $watch.Package am start-foreground-service --user 0 -n "$($watch.Package)/com.example.heart_radio.CaptureService" -a WIFI_START --es connection_code "'$taskCode'" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Wi-Fi 测试启动失败。' }
Write-Output '已请求启动本项目Wi-Fi测试；连接码未输出。请以电脑实际收到的packet为准。'
