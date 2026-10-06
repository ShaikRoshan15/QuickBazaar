@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv .venv
)
echo Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist ".env" copy /Y ".env.example" ".env" >nul
echo.
echo Starting QuickBazaar at http://127.0.0.1:5000
echo Press Ctrl+C to stop.
".venv\Scripts\python.exe" app.py
pause
