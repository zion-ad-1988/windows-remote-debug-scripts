@echo off
cd /d "%~dp0"
start "" pythonw gui_launcher.py <nul >nul 2>&1
exit
