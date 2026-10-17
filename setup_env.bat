@echo off
rem Create a local Python environment and install the scraper dependencies.
rem This avoids modifying the system-wide Python installation.
if not exist ".venv\Scripts\python.exe" (
    echo Creating project Python environment...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.12 -m venv .venv >nul 2>&1
        if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv >nul 2>&1
    )
    if not exist ".venv\Scripts\python.exe" (
        python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
        if not errorlevel 1 python -m venv .venv
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo Python 3.11 or newer with venv is required. Install it and try again.
    exit /b 1
)

".venv\Scripts\python.exe" -c "import ccxt, pandas, tzdata" >nul 2>&1
if errorlevel 1 (
    echo Installing scraper dependencies into .venv...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check the pip error above.
        exit /b 1
    )
)
".venv\Scripts\python.exe" -c "import ccxt, pandas, tzdata; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Seoul'); ZoneInfo('America/New_York')"
if errorlevel 1 (
    echo Scraper dependencies are unavailable in the project environment.
    exit /b 1
)
exit /b 0
