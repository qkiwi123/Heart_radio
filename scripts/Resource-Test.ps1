[CmdletBinding()]
param(
    [ValidateSet('Start','Status','Stop','Report','Check')][string]$Action='Start',
    [double]$Minutes=60,
    [double]$IntervalSeconds=5,
    [string]$RunDirectory,
    [switch]$Diagnostic
)
$ErrorActionPreference='Stop'
$resourceRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:PYTHONUTF8='1'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$resourceArgs=@((Join-Path $resourceRoot 'scripts\resource_monitor.py'),$Action.ToLowerInvariant(),
    '--root',$resourceRoot,'--minutes',$Minutes.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--interval',$IntervalSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))
if($RunDirectory){$resourceArgs+=@('--directory',$RunDirectory)}
if($Diagnostic){$resourceArgs+='--diagnostic'}
& (Join-Path $resourceRoot '.venv\Scripts\python.exe') @resourceArgs
if($LASTEXITCODE -ne 0){throw 'Resource test operation failed; see the message above.'}
