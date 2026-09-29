@echo off
setlocal EnableDelayedExpansion

:: Check and request Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Requesting administrative privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title Dune Awakening Route Synchronizer
echo ===================================================
echo   Dune: Awakening - Network Route Synchronizer
echo ===================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-DuneRoute.ps1"

if %errorLevel% neq 0 (
    echo.
    echo [-] Route synchronization encountered an error.
    echo ===================================================
    pause
    exit /b
)

echo.
echo ===================================================
echo   Synchronization Complete.
echo ===================================================
echo.

:PROMPT
set "USER_CHOICE="
set /p USER_CHOICE="Launch battlegroup.bat now? (Y/N): "

if /i "%USER_CHOICE%"=="Y" (
    if exist "%~dp0battlegroup.bat" (
        echo [*] Starting battlegroup.bat...
        start "" "%~dp0battlegroup.bat"
    ) else (
        echo [-] File battlegroup.bat was not found in the current folder.
        pause
    )
    exit /b
)

if /i "%USER_CHOICE%"=="N" (
    echo [*] Exiting without launching.
    timeout /t 2 >nul
    exit /b
)

echo [!] Invalid selection. Please enter Y or N.
goto PROMPT