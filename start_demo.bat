@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (py -3 run.py --demo %*) else (python run.py --demo %*)
if "%~1"=="" pause
