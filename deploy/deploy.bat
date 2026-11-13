@echo off
setlocal
if "%~1"=="" (
    echo.
    echo   Usage: deploy.bat SERVER_IP [PORT]
    echo   Example:  deploy.bat 203.0.113.10
    echo.
    pause
    exit /b 1
)
set "PORT=%~2"
if "%PORT%"=="" set "PORT=22"
echo Deploying to %1 (port %PORT%)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-from-windows.ps1" -Server %1 -Port %PORT%
echo.
pause