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
title Dune Awakening Network Controller
echo ===================================================
echo   Dune: Awakening - Network Controller
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

:: 3. Run synchronization phase
echo [*] Executing network synchronization...
"%PYTHON_EXE%" "%~dp0dune_net.py" sync
if errorlevel 1 (
    echo.
    echo [-] Synchronization encountered an error.
    echo ===================================================
    pause
    exit /b 1
)

:: 4. Run automated verification suite
echo.
echo [*] Executing verification suite...
"%PYTHON_EXE%" "%~dp0dune_net.py" verify
if errorlevel 1 (
    echo.
    echo [-] Network verification failed.
    echo ===================================================
    pause
    exit /b 1
)

echo.
echo ===================================================
echo   All systems operational and verified.
echo ===================================================
echo.

:: 5. Optional Battlegroup launch prompt
:PROMPT
set "USER_CHOICE="
set /p "USER_CHOICE=Launch battlegroup.bat now? (Y/N): "

if /i "%USER_CHOICE%"=="Y" (
    if exist "%~dp0battlegroup.bat" (
        echo [*] Launching battlegroup.bat...
        start "" "%~dp0battlegroup.bat"
    ) else (
        echo [-] battlegroup.bat not found in current folder.
        pause
    )
    goto FINAL_EXIT
)

if /i "%USER_CHOICE%"=="N" (
    echo [*] Exiting.
    goto FINAL_EXIT
)

echo [!] Invalid selection. Please enter Y or N.
goto PROMPT

:FINAL_EXIT
echo.
pause
exit /b 0