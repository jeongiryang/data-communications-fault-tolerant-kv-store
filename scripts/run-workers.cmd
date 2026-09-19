@echo off
chcp 65001 >nul
setlocal

if "%~1"=="" (
    echo 사용법: run-workers.cmd AWS_IP
    echo 예시: run-workers.cmd 12.34.56.78
    exit /b 1
)

cd /d "%~dp0\.."

where py >nul 2>nul
if errorlevel 1 (
    echo Python 실행 명령 'py'를 찾을 수 없습니다.
    exit /b 1
)

echo [1/4] Python 실행 환경을 준비합니다.
if not exist ".venv\Scripts\python.exe" (
    py -m venv .venv
    if errorlevel 1 exit /b 1
)

.venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 exit /b 1

echo [2/4] 테스트를 실행합니다.
.venv\Scripts\python.exe -m unittest discover -s tests -q
if errorlevel 1 exit /b 1

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "RUN_ID=%%i"
set "LOG_DIR=manual-test-logs\%RUN_ID%"
mkdir "%LOG_DIR%" >nul 2>nul

echo [3/4] Worker1부터 Worker4까지 실행합니다.
echo Master 주소: %~1:5000
echo Worker 로그: %LOG_DIR%

.venv\Scripts\python.exe -m kvstore.worker.launcher ^
    --master-host %~1 ^
    --master-port 5000 ^
    --p2p-host 127.0.0.1 ^
    --p2p-base-port 6001 ^
    --log-dir "%LOG_DIR%"
if errorlevel 1 exit /b 1

echo [4/4] Worker 정상 종료를 확인합니다.
powershell -NoProfile -Command "Select-String -Path '%LOG_DIR%\Worker*.txt' -SimpleMatch 'TERMINATE | SUCCESS'"
echo.
echo 네 줄이 출력되면 Worker 4개가 모두 정상 종료된 것입니다.
echo 로그 위치: %CD%\%LOG_DIR%

endlocal
