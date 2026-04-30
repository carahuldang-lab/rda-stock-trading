@echo off
REM One-click data refresh — news + fundamentals + analyst for ALL Nifty 500
REM Run this BEFORE running scan_universe.py for best signal coverage

cd /d "%~dp0..\"
call venv\Scripts\activate.bat

echo.
echo ============================================================
echo   REFRESH NEWS for top 200 Nifty 500 stocks (~3 min)
echo ============================================================
python -c "import sys; sys.path.insert(0, '.'); from agents.research.news_scraper import refresh_news; import pandas as pd; refresh_news(pd.read_csv('data/nifty500.csv')['symbol'].head(200).tolist())"

echo.
echo ============================================================
echo   REFRESH ANALYST REPORTS (top 200 stocks, ~3 min)
echo ============================================================
python -c "import sys; sys.path.insert(0, '.'); from agents.research.analyst_reports import refresh_reports; import pandas as pd; refresh_reports(pd.read_csv('data/nifty500.csv')['symbol'].head(200).tolist())"

echo.
echo ============================================================
echo   ALL DATA REFRESHED. Now run:
echo     python scripts\scan_universe.py --limit 500 --paper-trade --days 300
echo ============================================================
echo.
pause
