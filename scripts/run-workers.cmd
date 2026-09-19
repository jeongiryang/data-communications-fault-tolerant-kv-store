@echo off
setlocal

if "%~1"=="" (
    echo Usage: scripts\run-workers.cmd AWS_IP
    echo Example: scripts\run-workers.cmd 12.34.56.78
    exit /b 1
)

cd /d "%~dp0\.."

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher 'py' was not found.
    exit /b 1
)

echo [1/4] Preparing the Python environment...
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 exit /b 1
)

.venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 exit /b 1

echo [2/4] Running tests...
.venv\Scripts\python.exe -m unittest discover -s tests -q
if errorlevel 1 exit /b 1

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_ID=%%i"
set "LOG_DIR=manual-test-logs\%RUN_ID%"
mkdir "%LOG_DIR%" >nul 2>nul

echo [3/4] Starting Worker1 through Worker4...
echo Master: %~1:5000
echo Worker logs: %LOG_DIR%

.venv\Scripts\python.exe -m kvstore.worker.launcher ^
    --master-host %~1 ^
    --master-port 5000 ^
    --p2p-host 127.0.0.1 ^
    --p2p-base-port 6001 ^
    --log-dir "%LOG_DIR%"
if errorlevel 1 exit /b 1

echo [4/4] Checking normal Worker termination...
powershell -NoProfile -Command "Select-String -Path '%LOG_DIR%\Worker*.txt' -SimpleMatch 'TERMINATE | SUCCESS'"
echo.
echo Four matching lines mean that all Workers stopped normally.
echo Logs: %CD%\%LOG_DIR%

endlocal
