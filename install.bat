@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

if not "%XEROX_PYTHON%"=="" (
  "%XEROX_PYTHON%" "%ROOT%scripts\bootstrap.py" --shell cmd --launcher "xerox.bat"
  exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && py -3.12 "%ROOT%scripts\bootstrap.py" --shell cmd --launcher "xerox.bat" && exit /b %errorlevel%
  py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && py -3.11 "%ROOT%scripts\bootstrap.py" --shell cmd --launcher "xerox.bat" && exit /b %errorlevel%
  py -3.10 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && py -3.10 "%ROOT%scripts\bootstrap.py" --shell cmd --launcher "xerox.bat" && exit /b %errorlevel%
  py -3 "%ROOT%scripts\bootstrap.py" --shell cmd --launcher "xerox.bat"
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python "%ROOT%scripts\bootstrap.py" --shell cmd --launcher "xerox.bat"
  exit /b %errorlevel%
)

echo Python 3.10+ was not found. Install Python and rerun install.bat.
exit /b 1
