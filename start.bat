@echo off
rem Ear Disease Detection System - start the service and open the interface
rem Nawaloka Hospital PLC Research and Education Foundation

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  The environment is not set up yet. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo  No settings found. Run setup.bat to create the database and .env first.
    echo.
    pause
    exit /b 1
)

echo.
echo  Ear Disease Detection System
echo  ============================
echo.
echo  Loading the model, then opening http://localhost:5000
echo  Keep this window open while using the system. Close it to stop.
echo.

".venv\Scripts\python.exe" app.py --open

echo.
echo  The service has stopped.
pause
