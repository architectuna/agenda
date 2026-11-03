@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Compilar Agenda

echo.
echo  === 1/3  Verificando Python ===
where py >nul 2>nul
if errorlevel 1 (
    echo  No encuentro Python. Instalalo desde https://www.python.org/downloads/
    echo  ^(marca "Add python.exe to PATH" durante la instalacion^) y volve a ejecutar esto.
    pause & exit /b 1
)
py --version

echo.
echo  === 2/3  Instalando librerias y creando Agenda.exe ===
py -m pip install --upgrade --quiet pip
py -m pip install --upgrade --quiet requests icalendar recurring-ical-events tzdata tzlocal pyinstaller
if errorlevel 1 ( echo  Fallo la instalacion de librerias. & pause & exit /b 1 )

py -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name Agenda --icon agenda.ico --add-data "agenda.ico;." ^
    --collect-data tzdata --collect-submodules icalendar --hidden-import tzlocal ^
    agenda.pyw
if errorlevel 1 ( echo  Fallo PyInstaller. & pause & exit /b 1 )
echo  Listo: dist\Agenda.exe

echo.
echo  === 3/3  Armando el instalador ===
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo  No encuentro Inno Setup 6. Instalalo desde https://jrsoftware.org/isdl.php
    echo  y volve a ejecutar esto. Mientras tanto, dist\Agenda.exe ya funciona solo.
    pause & exit /b 0
)
"%ISCC%" /Q agenda.iss
if errorlevel 1 ( echo  Fallo Inno Setup. & pause & exit /b 1 )

echo.
echo  Instalador listo: instalador\Agenda-Setup.exe
explorer instalador
pause
