@echo off
title Hardware Shopping Assistant
cd /d "%~dp0"
echo ==========================================
echo   Hardware Shopping Assistant - START
echo ==========================================
echo.
echo [1/3] Check Taobao login status...
".venv\Scripts\python.exe" taobao_bot.py check
echo.
echo [2/3] Start gateway server (window 1)...
start "Gateway" cmd /k ".venv\Scripts\python.exe wecom_gateway.py"
timeout /t 4 /nobreak >nul
echo [3/3] Start cpolar tunnel (window 2)...
start "cpolar-Tunnel" cmd /k "tools\cpolar-portable\cpolar.exe http 8899 -log=stdout"
echo.
echo All started.
echo   - Window 1 (Gateway): server logs
echo   - Window 2 (Tunnel):  shows the public URL https://xxx.cpolar.top
echo   - Open that URL in your phone browser to use the assistant
echo   - If the URL changed after restart, use the new one
echo.
pause
