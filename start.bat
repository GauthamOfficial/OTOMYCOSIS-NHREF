@echo off
rem Ear Condition Screening - start the service and open the interface
rem Nawaloka Hospital Research and Education Foundation

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  The environment is not set up yet. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

echo.
echo  Ear Condition Screening
echo  =======================
echo.
echo  Loading the model, then opening http://localhost:5000
echo  Keep this window open while using the system. Close it to stop.
echo.

".venv\Scripts\python.exe" app.py --open

echo.
echo  The service has stopped.
pause
