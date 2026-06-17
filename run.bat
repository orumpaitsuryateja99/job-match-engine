@echo off
REM Double-click to launch the Job Automation app on Windows.
REM First run sets up a virtual environment and installs dependencies (~1 min).
setlocal
cd /d "%~dp0"
echo ============================================
echo   Resume-to-Job Automation
echo ============================================

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3 is not installed. Get it from https://www.python.org/downloads/
  echo During install, tick "Add Python to PATH", then run this again.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo First-time setup: creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 ( echo Could not create venv. & pause & exit /b 1 )
)

call ".venv\Scripts\activate.bat"
REM Install deps only when requirements.txt changed since last successful install
REM (keeps relaunches fast). Uses PowerShell for a reliable timestamp compare.
set "STAMP=.venv\.deps_ok"
set "NEEDINSTALL=1"
if exist "%STAMP%" (
  for /f %%R in ('powershell -NoProfile -Command "if((Get-Item 'requirements.txt').LastWriteTime -le (Get-Item '%STAMP%').LastWriteTime){'0'}else{'1'}"') do set "NEEDINSTALL=%%R"
)
if "%NEEDINSTALL%"=="1" (
  echo Installing/updating dependencies...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
  if not errorlevel 1 ( type nul > "%STAMP%" )
) else (
  echo Dependencies up to date.
)
echo.
echo Starting the app - your browser will open at http://localhost:8501
echo Leave this window open while you use it. Close it or press Ctrl+C to stop.
echo.
python -m streamlit run app/app.py
pause
