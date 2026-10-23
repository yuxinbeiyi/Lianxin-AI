@echo off
setlocal

cd /d "%~dp0"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

echo Starting Lianxin AI2 UI (tauri dev)...
echo Working dir: %CD%
echo.
call npm run tauri dev

if errorlevel 1 (
  echo.
  echo Tauri dev exited with code %errorlevel%.
  pause
)
