@echo off
cd /d "%~dp0"

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
