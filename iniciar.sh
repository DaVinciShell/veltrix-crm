#!/bin/sh
cd "$(dirname "$0")"
pip install -r requirements.txt -q
(sleep 2; command -v xdg-open >/dev/null && xdg-open http://localhost:5000 || open http://localhost:5000) &
python3 app.py
