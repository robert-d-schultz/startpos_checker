@echo off
rem Drag a start_pos .pack onto this file to check it. The game is settings.ini's
rem game (wh3 if unset); check_startpos_3k.bat checks a Three Kingdoms pack.
rem The report is shown here and saved next to this file as <pack name>_report.txt.
setlocal
set "HERE=%~dp0"

set "PY=py -3"
py -3 --version >nul 2>nul && goto :have_python
set "PY=python"
python --version >nul 2>nul && goto :have_python
echo Python 3 is not installed.
echo Get it from https://www.python.org/downloads/ and run this again.
goto :end

:have_python
%PY% -c "import websocket" >nul 2>nul && goto :have_packages
echo First run: installing the Python packages this tool needs...
%PY% -m pip install --user -r "%HERE%requirements.txt" && goto :have_packages
echo.
echo Installing the packages failed. Try this in a command prompt:
echo   %PY% -m pip install -r "%HERE%requirements.txt"
goto :end

:have_packages
set "PACK=%~1"
if not "%PACK%"=="" goto :have_pack
echo Drag your start_pos .pack file onto check_startpos.bat.
goto :end

:have_pack
for %%F in ("%PACK%") do set "REPORT=%HERE%%%~nF_report.txt"
if exist "%REPORT%" del "%REPORT%"
%PY% "%HERE%startpos_check.py" "%PACK%" %STARTPOS_GAME% --txt "%REPORT%"
if exist "%REPORT%" (
  echo.
  echo Report saved to "%REPORT%"
)

:end
echo.
pause
