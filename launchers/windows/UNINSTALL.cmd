@echo off
setlocal
set "ARCHIVER_ROOT=%~dp0"
if exist "%~dp0..\..\pyproject.toml" for %%I in ("%~dp0..\..") do set "ARCHIVER_ROOT=%%~fI\"
cd /d "%ARCHIVER_ROOT%"
if exist "app\twitter-x-archiver.exe" (
    "app\twitter-x-archiver.exe" --uninstall
) else (
    ".venv\Scripts\python.exe" -m twitter_x_archiver --uninstall
)
if errorlevel 1 pause
