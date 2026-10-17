@echo off
setlocal
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "LOCK_PATH=scraper.lock"
set "LOG_PATH=logs\scheduled_scraper.log"

if exist "%LOCK_PATH%" (
    echo Another scraper run appears to still be active. Exiting at %DATE% %TIME% with exit code 2. >> "%LOG_PATH%"
    echo Delete %LOCK_PATH% only if no scraper is actually running. >> "%LOG_PATH%"
    exit /b 2
)

type nul > "%LOCK_PATH%"

echo ------------------------------------------------------------ >> "%LOG_PATH%"
echo Run started at %DATE% %TIME%. >> "%LOG_PATH%"
echo Working directory: %CD% >> "%LOG_PATH%"

call setup_env.bat >> "%LOG_PATH%" 2>&1
if errorlevel 1 (
    set "EXIT_CODE=1"
    goto :finish
)

echo Running Binance-first historical scraper with MEXC fallback... >> "%LOG_PATH%"
".venv\Scripts\python.exe" --version >> "%LOG_PATH%" 2>&1
".venv\Scripts\python.exe" cryptodatav3.5.py >> "%LOG_PATH%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

:finish
echo Run finished at %DATE% %TIME% with exit code %EXIT_CODE%. >> "%LOG_PATH%"
echo ------------------------------------------------------------ >> "%LOG_PATH%"
echo. >> "%LOG_PATH%"

if exist "%LOCK_PATH%" del /f /q "%LOCK_PATH%" > nul 2>&1

exit /b %EXIT_CODE%
