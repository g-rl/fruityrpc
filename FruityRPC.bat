@echo off
setlocal
set "HERE=%~dp0"
set "PYEXE="

where py >nul 2>nul
if %ERRORLEVEL%==0 set "PYEXE=py -3"

if not defined PYEXE (
  where python >nul 2>nul
  if %ERRORLEVEL%==0 set "PYEXE=python"
)

if not defined PYEXE (
  echo.
  echo Python 3 was not found on this machine.
  echo Install it from https://www.python.org/downloads/ and tick
  echo "Add python.exe to PATH" during setup, then run this again.
  echo.
  if not defined FRUITYRPC_SILENT pause
  exit /b 1
)

%PYEXE% "%HERE%fruityrpc.py" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo FruityRPC exited with code %CODE%.
  if not defined FRUITYRPC_SILENT pause
)
exit /b %CODE%
