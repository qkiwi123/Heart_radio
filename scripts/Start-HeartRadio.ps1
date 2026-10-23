[CmdletBinding()]
param([string]$WatchAddress='',[ValidateRange(0,525600)][int]$Minutes,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$taskReceiverOptions=@{WatchAddress=$WatchAddress}
if ($PSBoundParameters.ContainsKey('Minutes')) { $taskReceiverOptions.Minutes=$Minutes }
if ($WatchAddress -or $PSBoundParameters.ContainsKey('Minutes')) { & "$PSScriptRoot\Start-Receiver.ps1" @taskReceiverOptions }
& "$PSScriptRoot\Start-Dashboard.ps1" -OpenBrowser:(!$NoBrowser) -EnsureCurrent
$taskBeforeStart=Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/connection' -TimeoutSec 10
& "$PSScriptRoot\Connection-Control.ps1" -Action start_if_enabled
if (!$taskBeforeStart.projectSession.suspended) { & "$PSScriptRoot\Connection-Control.ps1" -Action heart_receive_on }
