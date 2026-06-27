@ECHO OFF
CHCP 65001 >NUL

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
  py -3.8 _init_venv_and_requirements.py
) ELSE (
  ECHO venv exists, skipping dependency install
)

ECHO.
ECHO Starting helper from venv
set NO_PROXY=*
set no_proxy=*
.venv\Scripts\python main.py
