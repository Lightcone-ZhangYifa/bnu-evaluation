@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    python manage.py %*
) else (
    py -3 manage.py %*
)
if errorlevel 1 pause
