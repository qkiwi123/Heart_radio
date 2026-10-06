[CmdletBinding()]
param(
    [ValidateSet('Start','View','Stop','StopReceiver','ResourceStart','ResourceStatus','ResourceStop','ResourceReport')][string]$Action='Start',
    [switch]$CheckOnly
)
$ErrorActionPreference='Stop'
try {
    $launcherConfig=Join-Path $PSScriptRoot 'project-path.txt'
    if (!(Test-Path -LiteralPath $launcherConfig -PathType Leaf)) { throw 'Missing project-path.txt next to this launcher.' }
    $launcherRoot=[IO.File]::ReadAllText($launcherConfig,[Text.Encoding]::UTF8).Trim()
    if (![IO.Path]::IsPathRooted($launcherRoot)) { throw 'project-path.txt must contain an absolute project path.' }
    $launcherRoot=[IO.Path]::GetFullPath($launcherRoot)
    $launcherRoutes=@{
        Start='scripts\Start-HeartRadio.ps1'
        View='scripts\Start-Dashboard.ps1'
        Stop='scripts\Stop-HeartRadio.ps1'
        StopReceiver='scripts\Stop-Receiver.ps1'
        ResourceStart='scripts\Resource-Test.ps1'
        ResourceStatus='scripts\Resource-Test.ps1'
        ResourceStop='scripts\Resource-Test.ps1'
        ResourceReport='scripts\Resource-Test.ps1'
    }
    $launcherTarget=Join-Path $launcherRoot $launcherRoutes[$Action]
    if (!(Test-Path -LiteralPath $launcherTarget -PathType Leaf)) {
        throw "Project entry not found: $launcherTarget. Check project-path.txt."
    }
    if ($Action -in @('Start','View')) {
        foreach ($launcherDependency in @('.venv\Scripts\python.exe','dashboard\server.py','desktop-widget\build\HeartRadioWidgets.exe','scripts\capability_service.py','scripts\Capability-Service.ps1','scripts\data_policy.py','scripts\capability_duration.py','receiver\control.py','receiver\capability_bridge.py')) {
            if (!(Test-Path -LiteralPath (Join-Path $launcherRoot $launcherDependency) -PathType Leaf)) {
                throw "Required project file is missing: $launcherDependency"
            }
        }
    }
    if ($CheckOnly) {
        Write-Output "OK [$Action]: $launcherTarget"
        Write-Output 'Path/dependency check only; no service started or stopped.'
        exit 0
    }
    Push-Location -LiteralPath $launcherRoot
    try {
        if ($Action.StartsWith('Resource')) { & $launcherTarget -Action $Action.Substring(8) }
        elseif ($Action -eq 'View') { & $launcherTarget -OpenBrowser }
        else { & $launcherTarget }
    } finally { Pop-Location }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
