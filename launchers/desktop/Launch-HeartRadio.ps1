[CmdletBinding()]
param(
    [ValidateSet('Start','View','Stop','StopReceiver','ResourceStart','ResourceStatus','ResourceStop','ResourceReport')][string]$Action='Start',
    [switch]$CheckOnly,
    [switch]$NoBrowser
)
$ErrorActionPreference='Stop'
try {
    $launcherConfig=Join-Path $PSScriptRoot 'project-path.txt'
    $launcherConfiguredPath=if (Test-Path -LiteralPath $launcherConfig -PathType Leaf) {
        [IO.File]::ReadAllText($launcherConfig,[Text.Encoding]::UTF8).Trim()
    } else { '' }
    if (!$launcherConfiguredPath -or $launcherConfiguredPath -eq '<YOUR_PROJECT_ABSOLUTE_PATH>') {
        $launcherRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
        if (!(Test-Path -LiteralPath (Join-Path $launcherRoot 'Start-HeartRadio.cmd') -PathType Leaf)) {
            throw 'Outside the project, set project-path.txt next to this launcher to your absolute project path.'
        }
    } else {
        # IsPathRooted also accepts drive-relative paths such as C:folder on Windows.
        if ($launcherConfiguredPath -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
            throw 'project-path.txt must contain an absolute Windows path (drive and folder, or UNC share).'
        }
        $launcherRoot=[IO.Path]::GetFullPath($launcherConfiguredPath)
    }
    $launcherRoutes=@{
        Start='scripts\Start-HeartRadio.ps1'
        View='scripts\Start-Dashboard.ps1'
        Stop='scripts\Stop-HeartRadio.ps1'
        StopReceiver='scripts\Stop-HeartReception.ps1'
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
        if (!(Test-Path -LiteralPath (Join-Path $launcherRoot '.venv\Scripts\python.exe') -PathType Leaf)) {
            throw 'Missing .venv\Scripts\python.exe. Create a Python virtual environment in the project root (see the setup guide).'
        }
        foreach ($launcherDependency in @('dashboard\server.py','scripts\start_logged.py','scripts\capability_service.py','scripts\Capability-Service.ps1','scripts\data_policy.py','scripts\capability_duration.py','receiver\control.py','receiver\capability_bridge.py')) {
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
        elseif ($Action -eq 'Start') { & $launcherTarget -NoBrowser:$NoBrowser }
        else { & $launcherTarget }
    } finally { Pop-Location }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
