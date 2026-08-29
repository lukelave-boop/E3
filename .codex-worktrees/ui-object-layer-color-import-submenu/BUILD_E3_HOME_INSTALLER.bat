@echo off
setlocal
cd /d "%~dp0"
title Build Private E3 Home Installer

echo.
echo Building the private preconfigured E3 home installer...
echo This installer contains the household E3 bridge credential.
echo Do not upload it as a public release artifact.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_home_installer.ps1"
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
    echo Private E3 home installer completed successfully.
) else (
    echo Private E3 home installer failed with exit code %RESULT%.
)
echo.
pause
exit /b %RESULT%
