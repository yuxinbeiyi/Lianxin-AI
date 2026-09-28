@echo off
setlocal

cd /d "%~dp0"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

echo Starting Lianxin AI2 UI (tauri dev)...
echo Working dir: %CD%
echo.
call npm run tauri dev

echo.
echo Tauri dev exited with code %errorlevel%. You can close this window.
pause
