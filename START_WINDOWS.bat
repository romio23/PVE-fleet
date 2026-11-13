@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title PVE Fleet
set PYTHONUTF8=1
rem Build PyYAML without the C extension: then no compiler is needed (also with new Python versions)
set PYYAML_FORCE_LIBYAML=0

echo.
echo  PVE Fleet - starting on Windows
echo  ==============================
echo.

rem --- Find Python: preferably a common version via the py launcher ---
set "PY="
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
  if not defined PY py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
)
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
if not defined PY goto :nopython
for /f "delims=" %%I in ('%PY% -c "import sys; print(sys.version.split()[0])"') do echo  Python %%I found.

rem --- Own Python environment in the .venv folder, first time only ---
if not exist ".venv\Scripts\python.exe" (
  echo  First time: creating Python environment in the .venv folder ...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

rem --- Install packages if requirements.txt is new or changed ---
fc /b requirements.txt ".venv\req-installed.txt" >nul 2>nul
if errorlevel 1 (
  echo  Installing required packages, please wait ...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --prefer-binary -q -r requirements.txt
  if errorlevel 1 goto :fail
  copy /y requirements.txt ".venv\req-installed.txt" >nul
)

if not exist "data\config.yml" (
  echo  No data\config.yml found: the dashboard starts with sample data.
  echo  Your own servers? Copy config.example.yml to data\config.yml and fill it in.
)

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 goto :fail
goto :eof

:nopython
echo  Python 3.9 or newer was not found.
echo.
echo  Install Python in one of these ways and start this file again:
echo    - In PowerShell or the command prompt:  winget install Python.Python.3.13
echo    - Or via https://www.python.org/downloads/
echo      During the installation, tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:fail
echo.
echo  Something went wrong. Read the message above.
echo  Tip: delete the .venv folder and start this file again.
echo.
pause
exit /b 1
