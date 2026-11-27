#!/usr/bin/env bash
# Launch the Aux Audio Scrobbler GUI.
# Run this script from anywhere; it resolves the project root automatically.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure the virtual environment exists
if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: Virtual environment not found."
    echo "Run the following to set it up:"
    echo ""
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    echo ""
    exit 1
fi

source ./.venv/bin/activate
python3 gui.py
