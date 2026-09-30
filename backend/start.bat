@echo off
rem Double-click to start JARVIS. Needs Python 3.11+ from python.org.
rem First run: installs what it needs, then asks you for the Gemini key.
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python was not found. Install it from https://python.org
  echo and tick "Add Python to PATH", then double-click this again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time...
  %PY% -m venv .venv || goto :failed
)
.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :failed
.venv\Scripts\python.exe jarvis.py
pause
exit /b 0

:failed
echo Setup failed - see the messages above.
pause
exit /b 1
