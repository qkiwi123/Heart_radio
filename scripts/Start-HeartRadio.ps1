[CmdletBinding()]
param([string]$WatchAddress='',[ValidateRange(0,525600)][int]$Minutes,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
$taskReceiverOptions=@{WatchAddress=$WatchAddress}
if ($PSBoundParameters.ContainsKey('Minutes')) { $taskReceiverOptions.Minutes=$Minutes }
& "$PSScriptRoot\Start-Receiver.ps1" @taskReceiverOptions
& "$PSScriptRoot\Start-Dashboard.ps1" -OpenBrowser:(!$NoBrowser)
& "$PSScriptRoot\Capability-Service.ps1" -Action start-if-enabled
