[CmdletBinding()]
param([ValidateSet('start_if_enabled','start','shutdown','heart_receive_off','heart_receive_on')][string]$Action='start_if_enabled')
$ErrorActionPreference='Stop'
$taskBody=@{action=$Action}|ConvertTo-Json -Compress
$taskResponse=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8766/api/connection' -Method Post -ContentType 'application/json' -Headers @{'X-HeartRadio-Action'='connection'} -Body $taskBody -TimeoutSec 40
$taskState=$taskResponse.Content|ConvertFrom-Json
Write-Output "连接：$($taskState.status)；模式：$($taskState.configuredMode)"
