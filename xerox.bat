@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo Xerox is not installed yet. Run install.bat first.
  exit /b 1
)

cd /d "%ROOT%"
"%PYTHON%" -m xerox %*
exit /b %errorlevel%
