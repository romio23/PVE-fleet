@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title PVE Fleet
set PYTHONUTF8=1
rem PyYAML zonder C-extensie bouwen: dan is geen compiler nodig (ook bij nieuwe Python-versies)
set PYYAML_FORCE_LIBYAML=0

echo.
echo  PVE Fleet - starten op Windows
echo  ==============================
echo.

rem --- Python zoeken: bij voorkeur een gangbare versie via de py-launcher ---
set "PY="
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
  if not defined PY py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
)
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
if not defined PY goto :nopython
for /f "delims=" %%I in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo  Python %%I gevonden.

rem --- Eigen Python-omgeving in de map .venv, alleen de eerste keer ---
if not exist ".venv\Scripts\python.exe" (
  echo  Eerste keer: Python-omgeving aanmaken in de map .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

rem --- Pakketten installeren als requirements.txt nieuw of gewijzigd is ---
fc /b requirements.txt ".venv\req-installed.txt" >nul 2>nul
if errorlevel 1 (
  echo  Benodigde pakketten installeren, even geduld ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --prefer-binary -q -r requirements.txt
  if errorlevel 1 goto :fail
  copy /y requirements.txt ".venv\req-installed.txt" >nul
)

if not exist "data\config.yml" (
  echo  Geen data\config.yml gevonden: het dashboard start met voorbeelddata.
  echo  Eigen servers? Kopieer config.example.yml naar data\config.yml en vul die in.
)

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto :fail
goto :eof

:nopython
echo  Python 3.9 of nieuwer is niet gevonden.
echo.
echo  Installeer Python op een van deze manieren en start dit bestand opnieuw:
echo    - In PowerShell of de opdrachtprompt:  winget install Python.Python.3.13
echo    - Of via https://www.python.org/downloads/
echo      Vink tijdens de installatie "Add python.exe to PATH" aan.
echo.
pause
exit /b 1

:fail
echo.
echo  Er ging iets mis. Lees de melding hierboven.
echo  Tip: verwijder de map .venv en start dit bestand opnieuw.
echo.
pause
exit /b 1
