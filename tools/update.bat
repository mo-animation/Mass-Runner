@echo off
rem Started by the Update button: waits for Mass Runner to close, gets what changed on GitHub (files
rem changed only on this PC are kept), then opens it again. The first time on a PC it also installs Git if
rem needed and links the folder: the program files come from GitHub, settings.json, accounts.csv,
rem data\skip.json and data\runs stay as they are. Argument 1 = git.exe when the program found it.
cd /d "%~dp0.."
rem PyInstaller's variables of the Mass Runner that started this window: the exe opened again at the end would
rem take that closed one for its parent and stop ("invalid originating onefile parent process")
for /f "delims==" %%v in ('set _PYI_ 2^>nul') do set "%%v="
set PYINSTALLER_RESET_ENVIRONMENT=1
set "REPO=https://github.com/mo-animation/Mass-Runner.git"
set "GIT=%~1"
echo Waiting for Mass Runner to close...
:wait
tasklist /FI "IMAGENAME eq Mass Runner.exe" 2>nul | find /I "Mass Runner.exe" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait
)
if not defined GIT call :findgit
if not defined GIT (
    echo Installing Git - click Yes if Windows asks...
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements --silent
    call :findgit
)
if not defined GIT (
    echo.
    echo Git could not be installed. Install it from https://git-scm.com/download/win and press Update again.
    pause
    exit /b 1
)
rem safe.directory: git refuses a folder another Windows user (or a copy) owns; no fsync: some drives refuse it
rem ("fsync error on .git/objects/..: Bad file descriptor"); unpackLimit 1: a fetch keeps GitHub's pack file
rem instead of one file per object in .git\objects\xx
set G="%GIT%" -c safe.directory=* -c core.fsyncObjectFiles=false -c core.fsync=none -c fetch.unpackLimit=1
rem One block from here: git may replace this file, and cmd reads a batch file again after every command.
rem No commit yet (first update), or a pull that cannot go on (history rewritten on GitHub): take GitHub's
rem program files, settings.json kept.
(
    if not exist .git %G% init -q -b main
    %G% remote set-url origin %REPO% 2>nul || %G% remote add origin %REPO%
    echo Getting the changes from GitHub...
    %G% rev-parse -q --verify HEAD >nul && %G% pull --ff-only --autostash || (
        echo Taking the program files from GitHub - settings.json stays as it is...
        %G% fetch origin && %G% reset -q origin/main && %G% branch -q -u origin/main && %G% checkout origin/main -- . ":(exclude)settings.json"
    )
    if errorlevel 1 (
        echo.
        echo UPDATE FAILED. Send this window to Claude.
        pause
        exit /b 1
    )
    if not exist settings.json %G% checkout origin/main -- settings.json
    echo Done - opening Mass Runner
    rem ML_NO_OPEN is set by the tests only
    if not defined ML_NO_OPEN start "" "Mass Runner.exe"
    timeout /t 3 /nobreak >nul
    exit /b 0
)

:findgit
for %%i in (git.exe) do set "GIT=%%~$PATH:i"
if not defined GIT if exist "%ProgramW6432%\Git\cmd\git.exe" set "GIT=%ProgramW6432%\Git\cmd\git.exe"
if not defined GIT if exist "%ProgramFiles%\Git\cmd\git.exe" set "GIT=%ProgramFiles%\Git\cmd\git.exe"
if not defined GIT if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"
exit /b 0
