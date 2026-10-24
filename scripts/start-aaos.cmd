@echo off
rem Double-click to start AAOS: runs start-aaos.ps1 next to this file.
rem Prefers PowerShell 7 (pwsh) and falls back to Windows PowerShell.
rem -ExecutionPolicy Bypass applies to this one run only; no system setting changes.
setlocal
set "SCRIPT=%~dp0start-aaos.ps1"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
)
if not %ERRORLEVEL%==0 (
  echo.
  pause
)
