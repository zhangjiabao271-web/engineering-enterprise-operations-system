@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo 未找到工程虚拟环境，请先按仓库说明准备环境。
  pause
  exit /b 1
)
echo 打开隔离演示。所有演示录入均不影响正式账本。
".venv\Scripts\python.exe" -B "web_workbench\server.py" --demo "%TEMP%\engineering-web-demo-%RANDOM%-%RANDOM%" --port 0 --open-browser
if errorlevel 1 pause
endlocal
