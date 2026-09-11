@echo off
setlocal
set "ARCHIVER_ROOT=%~dp0"
if exist "%~dp0..\..\pyproject.toml" for %%I in ("%~dp0..\..") do set "ARCHIVER_ROOT=%%~fI\"
cd /d "%ARCHIVER_ROOT%"
if exist "app\twitter-x-archiver.exe" goto packaged
py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m twitter_x_archiver --install
if errorlevel 1 goto failed
pause
exit /b 0
:packaged
"app\twitter-x-archiver.exe" --install
pause
exit /b
:failed
echo Installation failed. Read the message above, then try again.
pause
exit /b 1
