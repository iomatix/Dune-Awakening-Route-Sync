@echo off
setlocal EnableDelayedExpansion

:: 1. Check Administrator Privileges
net session >nul 2>&1
if errorlevel 1 (
    echo [!] Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process cmd.exe -ArgumentList '/k \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"
title Dune Awakening Test Suite Runner
echo ===================================================
echo   Dune: Awakening - Automated Test Runner
echo ===================================================
echo.

:: 2. Locate Python binary
set "PYTHON_EXE="

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PYTHON_EXE=%%D\python.exe"
)

if "%PYTHON_EXE%"=="" (
    for /f "tokens=*" %%i in ('where python.exe 2^>nul') do (
        echo "%%i" | findstr /i /c:"WindowsApps" >nul
        if errorlevel 1 (
            if "!PYTHON_EXE!"=="" set "PYTHON_EXE=%%i"
        )
    )
)

if "%PYTHON_EXE%"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
    )
)

if "%PYTHON_EXE%"=="" (
    echo [-] CRITICAL: Valid Python 3 binary was not found.
    echo ===================================================
    pause
    exit /b 1
)

echo [+] Using Python runtime: "%PYTHON_EXE%"
echo.

:: 3. Execute test suite
"%PYTHON_EXE%" "%~dp0test_dune_net.py"
set "TEST_EXIT=%errorLevel%"

echo.
if %TEST_EXIT% equ 0 (
    echo ===================================================
    echo   TEST SUMMARY: ALL SYSTEMS VERIFIED AND PASSING
    echo ===================================================
) else (
    echo ===================================================
    echo   TEST SUMMARY: ASSERTION FAILURES DETECTED (Code: %TEST_EXIT%)
    echo ===================================================
)

echo.
pause
exit /b %TEST_EXIT%