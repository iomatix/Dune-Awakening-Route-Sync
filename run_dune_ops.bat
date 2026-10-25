@echo off
setlocal
cd /d "%~dp0"
title Dune: Awakening - Operator Console

echo [*] Starting Dune: Awakening Operator Console...
python -m dune_ops.app

if %ERRORLEVEL% NEQ 0 (
    echo [-] Application stopped with error code %ERRORLEVEL%.
    pause
)
endlocal