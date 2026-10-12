@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title video2pdf Web UI
pushd "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo [Error] Missing .venv\Scripts\python.exe.
    echo Please install the project environment as described in README.md.
    pause
    popd
    exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -u "web_ui.py" %*
set "WEB_EXIT_CODE=%ERRORLEVEL%"
popd
if not "%WEB_EXIT_CODE%"=="0" pause
exit /b %WEB_EXIT_CODE%
