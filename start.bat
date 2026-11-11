@echo off
setlocal
cd /d "%~dp0"
title Collections Copilot - Mandate Console

echo ============================================================
echo   Collections Copilot - Policy-Bound Autonomous Agent
echo ============================================================
echo.

:: 1. Ensure .env exists
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
    )
)

:: 2. Determine Python executable
set "PYTHON_EXE=python"
if exist "venv\Scripts\python.exe" (
    set "PYTHON_EXE=venv\Scripts\python.exe"
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
)

:: 3. Seed initial dataset if database does not exist yet
if not exist "copilot.db" (
    echo [*] First-time setup: verifying dependencies and seeding database...
    %PYTHON_EXE% -c "import fastapi, uvicorn, pydantic_settings, sqlalchemy, httpx, tenacity" >nul 2>&1
    if errorlevel 1 (
        %PYTHON_EXE% -m pip install -r requirements.txt
    )
    %PYTHON_EXE% scripts\seed_dataset.py
)

:: 4. Pick port 8000 (or next available port if another app is already using 8000)
for /f %%p in ('%PYTHON_EXE% -c "import socket; s=socket.socket(); res=s.connect_ex(('127.0.0.1', 8000)); s.close(); print(8000 if res!=0 else 8005)"') do set "PORT=%%p"

echo [*] Starting server on http://localhost:%PORT% (takes ~2 seconds)...
echo [*] Light Mode UI: http://localhost:%PORT%/ui/light
echo [*] Dark Mode UI:  http://localhost:%PORT%/ui/dark
echo [*] OpenAPI Docs:  http://localhost:%PORT%/docs
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:%PORT%"
%PYTHON_EXE% -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
pause
