@ECHO OFF
SETLOCAL
CHCP 65001 >NUL
CD /D "%~dp0"

set updated=0

ECHO.
ECHO Checking source updates
git fetch origin master --tags
git merge-base --is-ancestor origin/master HEAD
IF ERRORLEVEL 1 (
  ECHO Update found, pulling source
  git pull --ff-only origin master --tags
  set updated=1
) ELSE (
  ECHO Source is already up to date
)

ECHO.
IF NOT EXIST .venv\Scripts\python.exe (
  ECHO Initializing venv
  py -3.8 _init_venv_and_requirements.py
) ELSE IF "%updated%"=="1" (
  ECHO Source changed, checking dependencies
  .venv\Scripts\python _init_venv_and_requirements.py
) ELSE (
  ECHO venv exists, skipping dependency install
)

IF ERRORLEVEL 1 (
  ECHO Dependency initialization failed
  EXIT /B %ERRORLEVEL%
)

ECHO.
ECHO Starting MuMu chronicle tasks and helper from venv
set NO_PROXY=*
set no_proxy=*
IF "%SKIP_MUMU_CHRONICLE%"=="1" (
  .venv\Scripts\python main.py --no_max_console
) ELSE (
  .venv\Scripts\python run_with_mumu_chronicle.py
)

EXIT /B %ERRORLEVEL%
