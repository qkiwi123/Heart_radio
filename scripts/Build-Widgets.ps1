[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskFramework=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
if (!(Test-Path (Join-Path $taskFramework 'csc.exe'))) { $taskFramework=Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319' }
$taskCompiler=Join-Path $taskFramework 'csc.exe'
if (!(Test-Path $taskCompiler)) { throw '本机缺少.NET Framework C#编译器。' }
$taskOutput=Join-Path $taskRoot 'desktop-widget\build'
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$taskRefs=@('System.dll','System.Core.dll','System.Net.Http.dll','System.Web.Extensions.dll','System.Xaml.dll') | ForEach-Object { '/reference:'+(Join-Path $taskFramework $_) }
$taskRefs+=@('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object { '/reference:'+(Join-Path $taskFramework "WPF\$_") }
& $taskCompiler /nologo /utf8output /target:winexe /optimize+ /platform:anycpu ('/out:'+(Join-Path $taskOutput 'HeartRadioWidgets.exe')) @taskRefs (Join-Path $taskRoot 'desktop-widget\WidgetHost.cs')
if ($LASTEXITCODE -ne 0) { throw '桌面小窗编译失败。' }
Write-Output "已构建：$taskOutput\HeartRadioWidgets.exe"
