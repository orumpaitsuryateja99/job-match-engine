#!/bin/bash
# Double-click this file in Finder to launch the Job Automation app.
# First run sets up a virtual environment and installs dependencies (~1 min);
# later runs start instantly.

cd "$(dirname "$0")" || exit 1

echo "============================================"
echo "  Resume-to-Job Automation"
echo "============================================"

# 1) Need Python 3
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close..."
  exit 1
fi

# 2) Create the virtual environment on first run
if [ ! -d ".venv" ]; then
  echo "First-time setup: creating virtual environment..."
  python3 -m venv .venv || { echo "Could not create venv."; read -r -p "Press Enter..."; exit 1; }
fi

# 3) Activate + install dependencies (only when requirements.txt changed —
#    keeps day-to-day launches fast instead of re-resolving pip every time)
# shellcheck disable=SC1091
source .venv/bin/activate
STAMP=".venv/.deps_ok"
if [ ! -f "$STAMP" ] || [ "requirements.txt" -nt "$STAMP" ]; then
  echo "Installing/updating dependencies..."
  pip install --quiet --upgrade pip
  if pip install --quiet -r requirements.txt; then
    touch "$STAMP"
  fi
else
  echo "Dependencies up to date."
fi

# 4) Launch (opens your browser at http://localhost:8501)
echo ""
echo "Starting the app — your browser will open shortly."
echo "Leave this window open while you use it. Close it or press Ctrl+C to stop."
echo ""
python -m streamlit run app/app.py
