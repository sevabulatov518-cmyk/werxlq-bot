@echo off
title Telegram bot @Ve1vetz_bot
cd /d "%~dp0"

if not exist ".env" (
    echo.
    echo [!] .env not found.
    echo     Copy .env.example to .env and put your bot token inside.
    echo.
    pause
    exit /b 2
)

set "PYTHON="

rem --- 1) py launcher ---
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys" >nul 2>nul
if errorlevel 1 goto try_python
set "PYTHON=py -3"
goto run

rem --- 2) python from PATH (real one; Microsoft Store stub returns 9009) ---
:try_python
where python >nul 2>nul
if errorlevel 1 goto try_bundled
python -c "import sys" >nul 2>nul
if errorlevel 1 goto try_bundled
set "PYTHON=python"
goto run

rem --- 3) bundled Python from DeepSeek Harness ---
:try_bundled
for /d %%d in ("%USERPROFILE%\.dsh\dsh-runtimes\*") do (
    if exist "%%d\dependencies\python\python.exe" set "PYTHON="%%d\dependencies\python\python.exe""
)
if not defined PYTHON goto no_python
echo [i] No own Python found - using the Python bundled with DeepSeek Harness.
echo [i] For everyday use install Python 3.9+: https://www.python.org/downloads/
echo.
goto run

:no_python
echo.
echo [!] Python not found.
echo     Install Python 3.9+ from https://www.python.org/downloads/
echo     Tick "Add Python to PATH" during installation.
echo.
pause
exit /b 3

:run
if not "%~1"=="" goto run_bot
echo Starting bot... Press Ctrl+C to stop.
echo.
:run_bot
%PYTHON% bot.py %*

echo.
echo Bot stopped, exit code %ERRORLEVEL%.
pause