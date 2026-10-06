@echo off
cd /d "%~dp0"
where pwsh.exe >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-Receiver.ps1"
) else (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-Receiver.ps1"
)
pause
