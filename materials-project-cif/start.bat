@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if defined MP_CIF_PYTHON (
  "%MP_CIF_PYTHON%" "%~dp0download_cif.py" %*
) else if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0download_cif.py" %*
) else (
  python "%~dp0download_cif.py" %*
)
set "DOWNLOAD_EXIT=%ERRORLEVEL%"
echo.
echo Finished. Read the messages above for the result.
pause
exit /b %DOWNLOAD_EXIT%
