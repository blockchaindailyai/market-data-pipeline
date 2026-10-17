@echo off
setlocal
cd /d "%~dp0"

call setup_env.bat
if errorlevel 1 (
    echo Setup failed while creating the Python environment.
    exit /b 1
)

echo Installing all scraper requirements into .venv...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Setup failed while installing dependencies.
    exit /b 1
)

".venv\Scripts\python.exe" -c "import ccxt, pandas, tzdata; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Seoul'); ZoneInfo('America/New_York')"
if errorlevel 1 (
    echo Setup failed: dependencies or timezone data could not be imported.
    exit /b 1
)

echo Setup complete. Run run_scraper.bat to start the scraper.
exit /b 0
