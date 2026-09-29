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
title Dune Awakening Host Environment Setup
echo ===================================================
echo   Dune: Awakening - Host Environment Setup
echo ===================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Initialize-DuneHost.ps1"

if %errorLevel% neq 0 (
    echo.
    echo [-] Environment initialization encountered an error.
    echo ===================================================
    pause
    exit /b
)

echo.
echo ===================================================
echo   Setup Complete.
echo ===================================================
echo.
pause