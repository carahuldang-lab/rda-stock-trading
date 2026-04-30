@echo off
REM RDA Stock Trading — One-click startup script
REM Starts scheduler + dashboard + opens browser
REM Logs go to logs\scheduler.log and logs\dashboard.log

cd /d "%~dp0"

REM Activate venv
call venv\Scripts\activate.bat

REM Make sure logs folder exists
if not exist logs mkdir logs

REM Kill any previous instances (best effort)
taskkill /F /FI "WINDOWTITLE eq RDA Scheduler*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq RDA Dashboard*" >nul 2>&1

REM Start scheduler in a new visible CMD window so you can monitor
start "RDA Scheduler" cmd /k "venv\Scripts\activate.bat && python scheduler.py"

REM Wait 3 seconds for scheduler to settle
timeout /t 3 /nobreak >nul

REM Start dashboard in another window
start "RDA Dashboard" cmd /k "venv\Scripts\activate.bat && streamlit run dashboard\app.py"

REM Wait 8 seconds for streamlit to be ready, then open browser
timeout /t 8 /nobreak >nul
start http://localhost:8501

echo.
echo ============================================================
echo   RDA Stock Trading — STARTED
echo ============================================================
echo   Scheduler window: see "RDA Scheduler" CMD
echo   Dashboard window: see "RDA Dashboard" CMD
echo   Browser: http://localhost:8501
echo.
echo   To STOP everything: close both CMD windows.
echo ============================================================
echo.
echo This window will close automatically in 10 seconds...
timeout /t 10 /nobreak >nul
