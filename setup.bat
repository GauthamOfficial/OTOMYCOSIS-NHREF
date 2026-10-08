@echo off
rem Ear Condition Screening - one time setup
rem Nawaloka Hospital PLC Research and Education Foundation
rem
rem Creates a private Python environment in .venv and installs the
rem dependencies. Run once; afterwards use start.bat.

setlocal
cd /d "%~dp0"

echo.
echo  Ear Condition Screening - setup
echo  ===============================
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
