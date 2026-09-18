@echo off
cd /d "%~dp0"
set "TCL_LIBRARY=%~dp0.venv\tcl\tcl8.6"
set "TK_LIBRARY=%~dp0.venv\tcl\tk8.6"
.venv\Scripts\python.exe main.py
pause
