@echo off
setlocal

REM Unicorner Controller - double-click launcher (Windows)
REM Starts the Vite dev server in controller\ and opens the app in your default browser.

cd /d "%~dp0"

set "CONTROLLER_DIR=controller"
set "URL=http://localhost:5173/"

if not exist "%CONTROLLER_DIR%\" (
    echo ERROR: %CONTROLLER_DIR%\ not found in "%CD%"
    echo Make sure this launcher sits at the repo root next to the controller folder.
    echo.
    pause
    exit /b 1
)

cd "%CONTROLLER_DIR%"

where node >nul 2>nul
if errorlevel 1 goto :no_node
where npm >nul 2>nul
if errorlevel 1 goto :no_node

if not exist "node_modules\" (
    echo First run -- installing dependencies. This can take a minute...
    call npm install
    if errorlevel 1 (
        echo.
        echo npm install failed. See errors above.
        pause
        exit /b 1
    )
)

echo =================================================
echo   Unicorner Controller -- local launcher
echo =================================================
echo   Folder : %CD%
echo   URL    : %URL%
echo   Port   : 5173
echo.
echo   Make sure TouchDesigner is open with td\main.toe so
echo   the controller can connect on ws://127.0.0.1:9980.
echo.
echo   Closing this window will stop the dev server.
echo =================================================
echo.

REM Spawn a hidden PowerShell that waits ~4s for Vite to bind, then opens the URL.
REM Runs in the background so npm can stay in the foreground with its logs visible.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process '%URL%'"

REM Run Vite in the foreground; logs appear here. Ctrl+C or close window to stop.
call npm run dev

echo.
echo Dev server stopped.
pause
exit /b 0

:no_node
echo Node.js / npm is not installed.
echo Install the LTS build from https://nodejs.org and re-run this launcher.
echo.
pause
exit /b 1
