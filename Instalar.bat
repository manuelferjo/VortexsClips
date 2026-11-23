@echo off
chcp 65001 >nul
title Vortexs Clips - Instalar
echo Instalando lo necesario para Vortexs Clips (Python y ffmpeg)...
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo - Instalando Python...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
) else (
  echo - Python ya esta instalado.
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  if not exist "%~dp0ffmpeg\bin\ffmpeg.exe" (
    echo - Instalando ffmpeg...
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  )
) else (
  echo - ffmpeg ya esta instalado.
)
echo.
echo Listo. Cierra esta ventana y abre "Iniciar.bat".
echo (Si acabas de instalar algo, puede que tengas que reiniciar el PC una vez.)
pause
