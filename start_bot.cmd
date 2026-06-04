@echo off
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
echo === SOLDUIM start_bot.cmd ===
echo ROOT=%ROOT%
echo Stopping duplicate local pollers (if any)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\kill_soldium_main.ps1" -IncludeBareMainPy
where python 2>nul
for /f "delims=" %%A in ('where python 2^>nul') do (
  set "PYEXE=%%A"
  goto :havepy
)
echo ERROR: python not found on PATH. Install Python or add it to PATH.
pause
exit /b 1
:havepy
echo Using Python: %PYEXE%
powershell -NoProfile -Command "Write-Host ('main.py: ' + (Get-Item -LiteralPath '%ROOT%main.py').FullName); Write-Host ('main.py UTC mtime: ' + (Get-Item -LiteralPath '%ROOT%main.py').LastWriteTimeUtc.ToString('o'))"
echo Starting...
python "%ROOT%main.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Exit code %RC%
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
