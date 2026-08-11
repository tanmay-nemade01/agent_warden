@echo off
cd /d "%~dp0"
echo.
echo ============================================
echo   Notes Studio — starting server...
echo ============================================
echo.
python app\server.py %*
echo.
echo Server stopped.
pause
