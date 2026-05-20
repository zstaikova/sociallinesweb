@echo off
cd /d "%~dp0"

:: ── Cloudflare Tunnel ────────────────────────────────────────────────────────
:: Check if the Windows service is installed and running
sc query "Cloudflared" >nul 2>&1
if %errorlevel% equ 0 (
    sc query "Cloudflared" | findstr /i "RUNNING" >nul 2>&1
    if %errorlevel% neq 0 (
        echo Starting Cloudflared service...
        net start "Cloudflared" >nul 2>&1
    )
) else (
    :: Service not installed — fall back to a standalone process
    echo Cloudflare Tunnel service not installed. Starting standalone cloudflared...
    tasklist /FI "IMAGENAME eq cloudflared.exe" 2>nul | findstr /i "cloudflared.exe" >nul 2>&1
    if %errorlevel% neq 0 (
        start "" /min cloudflared tunnel --config "%~dp0cloudflared.yml" run
    )
)

:: ── Flask server ─────────────────────────────────────────────────────────────
:: Kill whatever process is listening on port 5000 (previous Flask server)
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":5000 " ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)

:: Also close any window titled "Socialline Server" (cleans up the terminal)
taskkill /FI "WINDOWTITLE eq Socialline Server" /F >nul 2>&1

timeout /t 2 /nobreak >nul

:: Open a new named server window
start "Socialline Server" cmd /k "cd /d "%~dp0" && python app/server.py"

exit