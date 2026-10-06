[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$widgetCheckRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$widgetCheckFramework=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (!(Test-Path (Join-Path $widgetCheckFramework 'csc.exe'))) { $widgetCheckFramework=Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319' }
$widgetCheckOutput=Join-Path $widgetCheckRoot 'desktop-widget\build\WidgetChecks.exe'
$widgetCheckRefs=@('System.dll','System.Core.dll','System.Net.Http.dll','System.Web.Extensions.dll','System.Xaml.dll') | ForEach-Object { '/reference:'+(Join-Path $widgetCheckFramework $_) }
$widgetCheckRefs+=@('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object { '/reference:'+(Join-Path $widgetCheckFramework "WPF\$_") }
$widgetCheckRefs+='/reference:'+(Join-Path $widgetCheckRoot 'desktop-widget\build\HeartRadioWidgets.exe')
& (Join-Path $widgetCheckFramework 'csc.exe') /nologo /utf8output /target:exe /optimize+ ('/out:'+$widgetCheckOutput) @widgetCheckRefs (Join-Path $widgetCheckRoot 'desktop-widget\WidgetChecks.cs')
if ($LASTEXITCODE -ne 0) { throw 'Widget checks could not be compiled.' }
# This process creates unshown WPF objects and an isolated test layout only.
& $widgetCheckOutput (Join-Path $widgetCheckRoot 'desktop-widget\build\check-layout.json')
if ($LASTEXITCODE -ne 0) { throw 'Widget component checks failed.' }
