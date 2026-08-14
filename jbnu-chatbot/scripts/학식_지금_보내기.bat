@echo off
chcp 65001 > nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_meal.ps1"
echo.
echo ------------------------------------------
echo  창을 닫으려면 아무 키나 누르세요
pause > nul
