@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY="%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
%PY% -m pip install -r requirements.txt
start http://localhost:5000
%PY% app.py
