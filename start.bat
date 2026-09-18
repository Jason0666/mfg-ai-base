@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "NODE22=D:\workspace\.tools\node22\node.exe"

title mfg-ai-base switch

REM ---- detect running (either port listening) ----
set "RUNNING="
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if %errorlevel%==0 set "RUNNING=1"
netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul
if %errorlevel%==0 set "RUNNING=1"

if defined RUNNING goto :stop
goto :start

:stop
echo ================================================
echo   mfg-ai-base is RUNNING -^> stopping ...
echo ================================================
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo [STOP] backend pid %%a
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    echo [STOP] frontend pid %%a
    taskkill /F /PID %%a >nul 2>&1
)
echo.
echo [OK] stopped. You can close the leftover service windows.
echo.
pause
exit /b

:start
echo ================================================
echo   mfg-ai-base is STOPPED -^> starting ...
echo ================================================
if not exist "%NODE22%" (
    echo [ERROR] node22 not found: %NODE22%
    pause
    exit /b 1
)

REM ---- backend :8000 ----
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [SKIP] backend already running on :8000
) else (
    echo [START] backend :8000 ...
    start "mfg-backend :8000" cmd /k "cd /d %ROOT%backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
    timeout /t 3 /nobreak >nul
)

REM ---- frontend :5173 ----
netstat -ano | findstr ":5173 " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [SKIP] frontend already running on :5173
) else (
    echo [START] frontend :5173 ...
    start "mfg-frontend :5173" cmd /k "cd /d %ROOT%frontend && "%NODE22%" node_modules\vite\bin\vite.js --port 5173"
    timeout /t 2 /nobreak >nul
)

REM ---- open browser ----
start "" http://localhost:5173

echo.
echo [OK] mfg-ai-base is ready
echo      Web      : http://localhost:5173
echo      API      : http://localhost:8000/api/health
echo      Access   : DEMO mode - login required or use a guest link (see /admin)
echo.
echo Tip: double-click start.bat again to STOP everything.
echo.
pause
