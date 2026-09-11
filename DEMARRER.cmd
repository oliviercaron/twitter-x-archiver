@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import server_control,webbrowser; s=server_control.start(); webbrowser.open('http://127.0.0.1:18765') if s.get('ok') else print('Le service ne demarre pas.')"
pause
