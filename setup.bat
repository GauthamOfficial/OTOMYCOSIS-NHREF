@echo off
rem NHREF OtoScan - one time setup
rem Nawaloka Hospital PLC Research and Education Foundation
rem
rem 1. Creates a private Python environment in .venv and installs dependencies.
rem 2. Runs the setup wizard: creates the PostgreSQL database, writes .env and
rem    creates the first administrator account.
rem
rem PostgreSQL must already be installed. See docs\SETUP_GUIDE.md.

setlocal
cd /d "%~dp0"

echo.
echo  NHREF OtoScan - setup
echo  =====================
echo.

if exist ".venv\Scripts\python.exe" (
    echo  Reusing the existing environment in .venv
    goto :install
)

rem TensorFlow 2.15 needs Python 3.9 to 3.11. Try the launcher first.
set "PY="
for %%V in (3.11 3.10 3.9) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)

rem Then whatever python is on PATH, if its version is supported.
if not defined PY (
    python -c "import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo  Python 3.9, 3.10 or 3.11 was not found.
    echo.
    echo  Install Python 3.11 from https://www.python.org/downloads/release/python-3119/
    echo  and tick "Add python.exe to PATH" during installation, then run this again.
    echo.
    pause
    exit /b 1
)

echo  Creating environment in .venv using %PY%
%PY% -m venv .venv
if errorlevel 1 goto :failed

:install
echo.
echo  Installing dependencies. TensorFlow is about 300 MB, this can take a while.
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt --timeout 120 --retries 10 --disable-pip-version-check
if errorlevel 1 goto :failed

if exist ".env" (
    echo.
    echo  Settings already exist in .env, skipping the database wizard.
    echo  To set up again, delete .env and run setup.bat.
    goto :done
)

echo.
echo  Database and administrator setup
echo  --------------------------------
echo  You will need the password chosen for the "postgres" user when
echo  PostgreSQL was installed. It is used once and not stored.
echo.
".venv\Scripts\python.exe" scripts\manage.py setup
if errorlevel 1 goto :failed

:done
echo.
echo  Setup complete. Double click start.bat to launch.
echo.
pause
exit /b 0

:failed
echo.
echo  Setup failed. Check the messages above, then run setup.bat again.
echo.
pause
exit /b 1
