@echo off
rem Double-click to open the generator in your browser.
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 studio.py %*
  goto done
)
where python >nul 2>nul
if not errorlevel 1 (
  python studio.py %*
  goto done
)
echo Python 3.9+ was not found. Install it from https://www.python.org/downloads/ and run this file again.
pause
exit /b 1
:done
if errorlevel 1 pause
