@echo off
cd /d "%~dp0"
if not exist work-temp mkdir work-temp
set "TEMP=%CD%\work-temp"
set "TMP=%TEMP%"
py -3.12 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Ready. Run start.bat and open http://127.0.0.1:8010
pause
exit /b 0
:failed
echo Setup failed. Install Python 3.12 and try again.
pause
exit /b 1
