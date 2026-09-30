@echo off
rem Double-click to open the Jarvis frontend in your browser.
rem Needs Python (python.org). Close this window to stop it.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 serve.py %* & goto :eof)
where python >nul 2>nul && (python serve.py %* & goto :eof)
echo Python was not found. Install it from https://python.org (tick "Add Python to PATH"),
echo or serve this folder with any static web server, e.g.  npx serve .
pause
