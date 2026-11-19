@echo off
chcp 65001 >nul
echo ========================================================
echo    FLOOR 2 - PHÒNG NGHE NHẠC YOUTUBE & CHAT REALTIME
echo ========================================================
echo.
echo Dang khoi dong may chu web tren http://localhost:8000 ...
echo.

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
