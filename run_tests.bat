@echo off
setlocal EnableDelayedExpansion

:: 1. Auto-elevate to Administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Requesting administrative privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title Dune Awakening - Test Suite Runner
echo ===================================================
echo   Dune: Awakening - Automated Test Runner
echo ===================================================
echo.

:: 2. Locate legitimate Python binary bypassing WindowsApps execution aliases
set "PYTHON_EXE="

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PYTHON_EXE=%%D\python.exe"
)

if "%PYTHON_EXE%"=="" (
    for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike '*WindowsApps*' } | Select-Object -ExpandProperty Source -First 1)"`) do (
        set "PYTHON_EXE=%%i"
    )
)

if "%PYTHON_EXE%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe -3"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [-] CRITICAL: Valid Python 3 binary was not found.
    echo ===================================================
    pause
    exit /b 1
)

echo [+] Using Python runtime: %PYTHON_EXE%
echo [*] Launching test suite...
echo.

:: 3. Execute test suite
%PYTHON_EXE% "%~dp0test_dune_net.py"

if %errorLevel% equ 0 (
    echo.
    echo ===================================================
    echo   TEST SUMMARY: ALL SYSTEMS VERIFIED AND PASSING
    echo ===================================================
) else (
    echo.
    echo ===================================================
    echo   TEST SUMMARY: ASSERTION FAILURES DETECTED
    echo ===================================================
)

echo.
pause