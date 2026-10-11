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
if "%~1"=="" (
  echo 打开正式账本的只读网页。不会修改账目；Ctrl+C 停止服务。
  echo 如需演示数据，请使用 run_web_demo.bat。
  ".venv\Scripts\python.exe" -B "web_workbench\server.py" --db "%~dp0supplier_data.db" --port 0 --open-browser
) else (
  ".venv\Scripts\python.exe" -B "web_workbench\server.py" %*
)
if errorlevel 1 pause
endlocal
