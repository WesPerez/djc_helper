@ECHO OFF
SETLOCAL
CHCP 65001 >NUL
CD /D "%~dp0"

REM 在任务栏可见的最小化窗口中运行，不抢占当前前台应用。
IF NOT "%DJC_HELPER_VISIBLE_CONSOLE%"=="1" (
  SET "DJC_HELPER_VISIBLE_CONSOLE=1"
  START "djc_helper 任务进度" /MIN CMD.EXE /D /C CALL "%~f0"
  EXIT /B 0
)

TITLE djc_helper - MuMu / main.py 任务进度
ECHO ================================================================
ECHO djc_helper 完整任务流程
ECHO 启动时间：%DATE% %TIME%
ECHO MuMu 阶段结束后会在本窗口继续运行 main.py。
ECHO 本窗口保持最小化运行，全部任务结束后会自动关闭。
ECHO ================================================================

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
ECHO Starting MuMu chronicle tasks and helper from venv
set NO_PROXY=*
set no_proxy=*
IF "%SKIP_MUMU_CHRONICLE%"=="1" (
  .venv\Scripts\python main.py --no_max_console
) ELSE (
  .venv\Scripts\python run_with_mumu_chronicle.py
)

SET "task_exit_code=%ERRORLEVEL%"
ECHO.
ECHO ================================================================
IF "%task_exit_code%"=="0" (
  TITLE djc_helper - 任务已完成
  ECHO 完整任务已成功结束：%DATE% %TIME%
) ELSE (
  TITLE djc_helper - 任务异常结束
  ECHO 任务异常结束，退出码：%task_exit_code%
  ECHO 结束时间：%DATE% %TIME%
)
ECHO 控制台窗口即将自动关闭，详细记录保存在 logs 目录。
ECHO ================================================================

EXIT /B %task_exit_code%
