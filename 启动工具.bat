@echo off
cd /d "%~dp0"
set "APP=%~dp0app.pyw"

where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "%APP%"
    exit /b
)

where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe "%APP%"
    exit /b
)

where py.exe >nul 2>nul
if not errorlevel 1 (
    start "" py.exe -3 "%APP%"
    exit /b
)

echo.
echo 没有找到 Python。
echo 请先安装 Python 3.9 或更高版本（https://www.python.org/downloads/ ），
echo 安装时务必勾选 "Add python.exe to PATH"。
echo.
pause
