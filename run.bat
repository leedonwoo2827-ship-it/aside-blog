@echo off
rem aside-blog launcher.
rem   run.bat               open the dashboard (server + window)
rem   run.bat make --job X  any CLI command: python -m aside_blog ...
rem NOTE: .bat files must stay ASCII + CRLF (Korean cmd reads UTF-8/LF wrong).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
if "%~1"=="" (
  title aside-blog - KEEP THIS WINDOW OPEN while posts are queued - minimize is OK
  echo.
  echo   aside-blog is running. Keep this window open - minimize is OK.
  echo   Closing this window stops app-queued posts - Naver reserved posts are not affected.
  echo.
  ".venv\Scripts\python" -m aside_blog ui
) else (
  ".venv\Scripts\python" -m aside_blog %*
)
