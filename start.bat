@echo off
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv "%VENV%"
  ) else (
    python -m venv "%VENV%"
  )
  if errorlevel 1 goto setup_error
  "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto setup_error
)
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app.pyw"
exit /b 0

:setup_error
echo DP104 Studio setup failed. Check Python and network access, then retry.
pause
exit /b 1
