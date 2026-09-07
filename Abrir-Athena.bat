@echo off
chcp 65001 >nul
title Athena - Asistente Inmobiliario
cd /d "%~dp0"
cls

echo.
echo   ===============================================
echo      ATHENA - Asistente Inmobiliario
echo   ===============================================
echo.

REM --- 1. Python instalado? ---
where python >nul 2>&1
if errorlevel 1 (
  echo   [X] No encuentro Python en esta computadora.
  echo.
  echo   Instalalo desde https://www.python.org/downloads/
  echo   IMPORTANTE: al instalar, marca la casilla
  echo   "Add python.exe to PATH" o no va a funcionar.
  echo.
  pause
  exit /b 1
)

REM --- 2. Existe el archivo de llaves? ---
if not exist ".env" (
  echo   [!] Falta el archivo .env con las llaves.
  echo.
  if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo   Te cree uno a partir de .env.example.
    echo   Se va a abrir en el Bloc de notas: pon tu
    echo   ANTHROPIC_API_KEY, guarda y cierra el Bloc.
    echo.
    pause
    notepad .env
  ) else (
    echo   Tampoco esta .env.example. Copia la carpeta completa
    echo   desde la computadora donde si funciona.
    pause
    exit /b 1
  )
)

REM --- 3. Falta alguna libreria? Solo la primera vez ---
python -c "import fastapi, anthropic, uvicorn, httpx, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo   Primera vez en esta computadora.
  echo   Instalando lo necesario, tarda un par de minutos...
  echo.
  python -m pip install --quiet --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   [X] Fallo la instalacion. Revisa tu conexion a internet.
    pause
    exit /b 1
  )
  echo   Listo.
  echo.
)

REM --- 4. Arrancar ---
echo   Iniciando Athena...
echo.
echo   Para ENSENARLA:      http://localhost:8100/?inquilino=rednorte
echo   Panel del asesor:    http://localhost:8100/panel
echo.
echo   El navegador se abre solo en unos segundos.
echo   Para APAGARLA: cierra esta ventana negra.
echo.
echo   ===============================================
echo.

REM Abre el navegador 5 segundos despues, ya que el servidor levanto.
start "" /min cmd /c "timeout /t 5 >nul & start "" http://localhost:8100/?inquilino=rednorte"

python -m inmobot.servidor

echo.
echo   Athena se detuvo.
pause
