#!/bin/bash
# Install dependencies
pip install -r backend/requirements.txt

# Run the backend server
echo "Starting Dyslexia Reader Backend..."
python3 backend/main.py
