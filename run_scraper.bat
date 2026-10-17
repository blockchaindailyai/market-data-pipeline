@echo off
setlocal
cd /d "%~dp0"

call setup_env.bat
if errorlevel 1 (
  set "EXIT_CODE=1"
  goto :result
)

echo Running Binance-first historical scraper with MEXC fallback...
".venv\Scripts\python.exe" cryptodatav3.5.py
set "EXIT_CODE=%ERRORLEVEL%"

:result
echo.
if %EXIT_CODE% neq 0 (
  echo Scraper run failed with exit code %EXIT_CODE%.
) else (
  echo Scraper run completed successfully.
)

echo Press any key to close this window...
pause >nul
exit /b %EXIT_CODE%
