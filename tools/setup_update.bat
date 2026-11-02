@echo off
rem Run ONCE on a PC (double-click): links this Mass Runner folder to GitHub so the Update button works.
rem The program files come from GitHub; your settings.json, accounts.csv, data\skip.json and data\runs
rem stay exactly as they are.
cd /d "%~dp0.."
set "GIT=C:\Program Files\Git\cmd\git.exe"
set "REPO=https://github.com/mo-animation/Mass-Runner.git"

if exist .git (
    echo This folder is already linked to GitHub - use the Update button in Mass Runner.
    pause
    exit /b 0
)
tasklist /FI "IMAGENAME eq Mass Runner.exe" 2>nul | find /I "Mass Runner.exe" >nul
if not errorlevel 1 (
    echo Close Mass Runner first, then run this again.
    pause
    exit /b 1
)
if not exist "%GIT%" (
    echo Installing Git - click Yes when Windows asks...
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
)
if not exist "%GIT%" (
    echo Git could not be installed.
    pause
    exit /b 1
)
"%GIT%" config --global --add safe.directory "%CD:\=/%"
"%GIT%" init -q -b main
"%GIT%" remote add origin %REPO%
echo Connecting to GitHub...
"%GIT%" fetch origin
if errorlevel 1 (
    rmdir /s /q .git
    echo Could not reach the repository - nothing was changed.
    pause
    exit /b 1
)
"%GIT%" reset -q origin/main
"%GIT%" branch -q -u origin/main
rem every program file as on GitHub, your own settings.json kept
"%GIT%" checkout origin/main -- . ":(exclude)settings.json"
echo.
echo Done. From now on press Update in Mass Runner.
"%GIT%" status --short
pause
