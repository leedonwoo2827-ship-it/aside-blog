#!/usr/bin/env bash
# aside-blog launcher for macOS / Linux.
#   ./run.sh               open the dashboard (server + window)
#   ./run.sh make --job X  any CLI command: python -m aside_blog ...
# Windows uses run.bat instead.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if [ $# -eq 0 ]; then
  printf '\033]0;aside-blog - keep this window open while posts are queued\007'
  echo "aside-blog is running. Keep this terminal open - minimize is OK."
  echo "Closing it stops app-queued posts - Naver reserved posts are not affected."
  exec .venv/bin/python -m aside_blog ui
else
  exec .venv/bin/python -m aside_blog "$@"
fi
