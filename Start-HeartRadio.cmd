@echo off
cd /d "%~dp0"
where pwsh.exe >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-HeartRadio.ps1"
) else (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-HeartRadio.ps1"
)
if errorlevel 1 pause
