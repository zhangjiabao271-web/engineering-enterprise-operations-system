@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment was not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

if exist ".venv\tcl\tcl8.6\init.tcl" (
    set "TCL_LIBRARY=%~dp0.venv\tcl\tcl8.6"
)
if exist ".venv\tcl\tk8.6\tk.tcl" (
    set "TK_LIBRARY=%~dp0.venv\tcl\tk8.6"
)

".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo [ERROR] The application exited unexpectedly.
    pause
    exit /b 1
)
