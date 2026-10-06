@echo off
setlocal
chcp 65001 >nul
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-HeartRadio.ps1" -Action ResourceReport
set "launcherExitCode=%errorlevel%"
pause
exit /b %launcherExitCode%
