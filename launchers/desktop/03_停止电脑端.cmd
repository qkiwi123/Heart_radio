@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-HeartRadio.ps1" -Action Stop
set "launcherExitCode=%errorlevel%"
pause
exit /b %launcherExitCode%
