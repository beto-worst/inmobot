@echo off
chcp 65001 >nul
title Athena - en internet
cd /d "%~dp0"
cls

echo.
echo   ===============================================
echo      ATHENA - con direccion publica
echo   ===============================================
echo.
echo   Esto abre Athena y le pone una direccion de
echo   internet, para que el chat de tu pagina pueda
echo   hablarle desde cualquier lado.
echo.
echo   OJO:
echo    - Athena vive mientras esta ventana este abierta.
echo      Si apagas la compu, el chat deja de contestar.
echo    - La direccion cambia cada vez que lo abres.
echo    - Cualquiera que tenga la direccion puede chatear,
echo      y cada mensaje cuesta. Por eso hay topes: 20
echo      mensajes por persona cada 10 minutos y 400 al dia.
echo.

set CLOUDFLARED="C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not exist %CLOUDFLARED% (
  echo   [X] No encuentro cloudflared.
  echo       Instalalo con:  winget install Cloudflare.cloudflared
  echo.
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo   [X] No encuentro Python. Instalalo desde python.org
  echo       y marca "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

echo   ===============================================
echo.
echo   1) Se abre una ventana con Athena (dejala abierta).
echo   2) Aqui abajo va a salir una direccion que termina
echo      en trycloudflare.com. ESA es la que me pasas.
echo.
echo   ===============================================
echo.

start "Athena" cmd /k "cd /d "%~dp0" && python -m inmobot.servidor"

echo   Esperando a que Athena levante...
timeout /t 6 >nul
echo.

%CLOUDFLARED% tunnel --url http://127.0.0.1:8100 --no-autoupdate

echo.
echo   La direccion publica se cerro. Athena sigue corriendo
echo   en la otra ventana, pero ya solo desde esta compu.
pause
