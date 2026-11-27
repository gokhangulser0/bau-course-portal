#!/usr/bin/env bash
# Start the Local Desktop UI on this computer only (127.0.0.1:8502). Python is found automatically.
cd "$(dirname "$0")" || exit 1
APP=desktop_app.py ADDRESS=127.0.0.1 exec bash run_lan.sh "${1:-8502}"
