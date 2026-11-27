#!/usr/bin/env bash
# Start the portal for local network (LAN) testing on macOS / Linux without relying on "python" on PATH.
#   bash run_lan.sh            # find Python + Streamlit and start on 0.0.0.0:8501
#   bash run_lan.sh 8502       # another port
#   SETUP=1 bash run_lan.sh    # create .venv and install requirements.txt without asking
cd "$(dirname "$0")" || exit 1
PORT="${1:-8501}"
APP="${APP:-app.py}"
ADDRESS="${ADDRESS:-0.0.0.0}"
PROBE="import sys, importlib.util as u; s=u.find_spec('streamlit'); print(sys.executable); print('%d.%d.%d' % sys.version_info[:3]); print(__import__('streamlit').__version__ if s else '')"

cands=()
for d in .venv venv env; do [ -x "$d/bin/python" ] && cands+=("$d/bin/python"); done
[ -n "$CONDA_PREFIX" ] && [ -x "$CONDA_PREFIX/bin/python" ] && cands+=("$CONDA_PREFIX/bin/python")
for r in "$HOME/anaconda3" "$HOME/miniconda3" "$HOME/miniforge3" /opt/anaconda3 /opt/miniconda3; do
  [ -x "$r/bin/python" ] && cands+=("$r/bin/python")
  for e in "$r"/envs/*/bin/python; do [ -x "$e" ] && cands+=("$e"); done
done
for n in python3 python; do p="$(command -v "$n" 2>/dev/null)"; [ -n "$p" ] && cands+=("$p"); done
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 /Library/Frameworks/Python.framework/Versions/3.*/bin/python3; do
  [ -x "$p" ] && cands+=("$p")
done

echo "== Looking for Python =="
SEL=""; BASE=""; SEEN=" "
for c in "${cands[@]}"; do
  out="$("$c" -c "$PROBE" 2>/dev/null)" || continue
  exe="$(echo "$out" | sed -n 1p)"; ver="$(echo "$out" | sed -n 2p)"; st="$(echo "$out" | sed -n 3p)"
  case "$SEEN" in *" $exe "*) continue;; esac
  SEEN="$SEEN$exe "
  echo "  $exe (Python $ver, ${st:+Streamlit $st}${st:-no Streamlit})"
  [ -z "$BASE" ] && BASE="$c"
  if [ -n "$st" ] && [ -z "$SEL" ]; then SEL="$c"; fi
done
if [ -z "$BASE" ]; then
  echo "No working Python interpreter was found. Install Python 3.12+ (python.org or Homebrew: brew install python) and run again."
  exit 1
fi
if [ -z "$SEL" ]; then
  echo "Python was found, but Streamlit is not installed."
  ans="y"; [ -z "$SETUP" ] && read -r -p "Create .venv with $BASE and install requirements.txt? [Y/n] " ans
  case "$ans" in [nN]*) echo "Cancelled."; exit 1;; esac
  "$BASE" -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install -r requirements.txt || exit 1
  if ! .venv/bin/python -c "import streamlit" 2>/dev/null; then
    echo "Streamlit is still not available. Check the pip output above (internet connection, proxy)."
    exit 1
  fi
  SEL=".venv/bin/python"
fi
echo "Using: $SEL"
if [ "$ADDRESS" = "127.0.0.1" ]; then
  echo "== Open http://localhost:$PORT on this computer (other devices cannot reach it) =="
  export QS_LAUNCHER="run_desktop.sh"
  exec "$SEL" -m streamlit run "$APP" --server.address "$ADDRESS" --server.port "$PORT"
fi
echo "== Open one of these on another device on the same network =="
if command -v ipconfig >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
  for i in en0 en1 en2; do ip="$(ipconfig getifaddr $i 2>/dev/null)"; [ -n "$ip" ] && echo "  http://$ip:$PORT"; done
else
  for ip in $(hostname -I 2>/dev/null); do case "$ip" in *:*) ;; *) echo "  http://$ip:$PORT";; esac; done
fi
echo "  On this computer: http://localhost:$PORT"
echo "Firewall: macOS - allow incoming connections for Python; Linux (ufw) - sudo ufw allow $PORT/tcp"
export QS_LAUNCHER="run_lan.sh"
exec "$SEL" -m streamlit run "$APP" --server.address "$ADDRESS" --server.port "$PORT"
