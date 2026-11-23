@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if errorlevel 1 (
  echo No encuentro Python. Ejecuta primero Instalar.bat
  pause
  exit /b
)
start "" pyw app.py
