@echo off
REM Stop all RDA Stock Trading processes

taskkill /F /FI "WINDOWTITLE eq RDA Scheduler*" 2>nul
taskkill /F /FI "WINDOWTITLE eq RDA Dashboard*" 2>nul

REM Kill any orphan streamlit / python scheduler processes
for /f "tokens=2" %%i in ('tasklist ^| findstr /I "streamlit.exe" 2^>nul') do taskkill /F /PID %%i >nul 2>&1

echo RDA Stock Trading — STOPPED
timeout /t 3 /nobreak >nul
