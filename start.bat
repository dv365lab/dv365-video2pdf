@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title video2pdf
pushd "%~dp0"
if errorlevel 1 (
    echo [Error] Cannot open the project directory.
    pause
    exit /b 1
)
setlocal EnableDelayedExpansion
rem Parse the complete block before Python waits for input or the file is updated.
(
    if exist ".venv\Scripts\python.exe" (
        ".venv\Scripts\python.exe" -X utf8 -u "launcher.py"
        set "LAUNCH_EXIT_CODE=!ERRORLEVEL!"
    ) else (
        echo [Error] Missing .venv\Scripts\python.exe.
        echo Please install the project environment as described in README.md.
        pause
        set "LAUNCH_EXIT_CODE=1"
    )
    popd
    exit /b !LAUNCH_EXIT_CODE!
)
