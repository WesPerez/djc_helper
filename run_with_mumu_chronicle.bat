@ECHO OFF
CHCP 65001 >NUL

CD /D "%~dp0"

IF EXIST ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run_with_mumu_chronicle.py %*
) ELSE (
  python run_with_mumu_chronicle.py %*
)
