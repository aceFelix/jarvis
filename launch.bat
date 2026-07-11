@echo off
chcp 65001 >nul 2>&1
cd /d "E:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis"

:: Activate venv silently
call .venv\Scripts\activate.bat >nul 2>&1

:: Step 1: launch daemon in background
start "" /B jarvis --daemon
ping 127.0.0.1 -n 3 >nul

:: Step 2: launch REPL in foreground
jarvis
