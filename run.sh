#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

# Check for ffmpeg (required for Whisper + audio splitting)
if ! command -v ffmpeg &>/dev/null; then
    echo "Error: ffmpeg is required but not found. Install it and try again." >&2
    exit 1
fi

# Install Python dependencies
pip install -q -r backend/requirements.txt

# Start the server
echo "Starting Audiolens on http://127.0.0.1:8000"
python3 backend/main.py
