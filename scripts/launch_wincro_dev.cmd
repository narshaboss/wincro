@echo off
setlocal

set "ROOT=%~dp0.."
set "PYTHON_EXE=C:\Users\dadat\AppData\Local\Programs\Python\Python310\pythonw.exe"
set "LOG_DIR=%ROOT%\logs"
set "LOG_FILE=%LOG_DIR%\dev_launcher.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
echo [%DATE% %TIME%] launch requested root=%ROOT% python=%PYTHON_EXE% >> "%LOG_FILE%"

if not exist "%PYTHON_EXE%" (
  echo [%DATE% %TIME%] missing Python executable: %PYTHON_EXE% >> "%LOG_FILE%"
  exit /b 1
)

cd /d "%ROOT%"
set "WINCRO_DEV_NO_ADMIN=1"
start "" "%PYTHON_EXE%" "%ROOT%\src\main.py"
exit /b %ERRORLEVEL%
