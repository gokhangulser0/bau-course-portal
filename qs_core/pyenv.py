"""Find Python interpreters (and whether Streamlit is installed in them) without assuming that
`python` is on PATH. Mirrors the search order of run_lan.ps1 / run_lan.sh:

  1. virtual environments in this folder: .venv, venv, env
  2. the active Conda environment (CONDA_PREFIX) and common Anaconda/Miniconda installs
  3. the Windows Python launcher: py -3 (and every interpreter `py -0p` lists)
  4. python3 / python on PATH (the Microsoft Store "python" alias is ignored when it is only a stub)
  5. common installation folders (python.org on Windows, Homebrew / python.org on macOS)
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

from .common import BASE_DIR

IS_WIN = os.name == "nt"
_PROBE = ("import sys, importlib.util as u; s = u.find_spec('streamlit'); "
          "v = __import__('streamlit').__version__ if s else ''; "
          "print(sys.executable); print('%d.%d.%d' % sys.version_info[:3]); print(v)")


def _venv_python(root: str) -> str:
    return os.path.join(root, "Scripts", "python.exe") if IS_WIN else os.path.join(root, "bin", "python")


def candidates() -> list[tuple[str, list[str], str]]:
    """[(label, command, source)]: command is the argv prefix that starts the interpreter."""
    out = []

    def add(cmd, source):
        if cmd and cmd not in [c for _, c, _ in out]:
            out.append((" ".join(cmd), cmd, source))

    for d in (".venv", "venv", "env"):
        p = _venv_python(os.path.join(BASE_DIR, d))
        if os.path.isfile(p):
            add([p], f"Virtual environment ({d})")
    if os.environ.get("CONDA_PREFIX"):
        p = os.path.join(os.environ["CONDA_PREFIX"], "python.exe" if IS_WIN else "bin/python")
        if os.path.isfile(p):
            add([p], "Active Conda environment")
    home = os.path.expanduser("~")
    conda_roots = [os.path.join(home, n) for n in ("anaconda3", "miniconda3", "Anaconda3", "Miniconda3",
                                                   "miniforge3", "mambaforge")]
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        conda_roots += [os.path.join(la, n) for n in ("anaconda3", "miniconda3")] + [r"C:\ProgramData\Anaconda3",
                                                                                     r"C:\ProgramData\Miniconda3"]
    for root in conda_roots:
        base = os.path.join(root, "python.exe") if IS_WIN else os.path.join(root, "bin", "python")
        if os.path.isfile(base):
            add([base], "Conda base")
        for env_dir in glob.glob(os.path.join(root, "envs", "*")):
            env_py = _venv_python(env_dir) if not IS_WIN else os.path.join(env_dir, "python.exe")
            if os.path.isfile(env_py):
                add([env_py], f"Conda env ({os.path.basename(env_dir)})")
    if IS_WIN and shutil.which("py"):
        add(["py", "-3"], "Windows Python launcher")
        try:
            r = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=8)
            for line in r.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[-1].lower().endswith("python.exe") and os.path.isfile(parts[-1]):
                    add([parts[-1]], "Listed by py -0p")
        except Exception:
            pass
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p:                      # the Microsoft Store stub is filtered out later because it does not run
            add([p], f"'{name}' on PATH")
    if IS_WIN:
        la = os.environ.get("LOCALAPPDATA", "")
        pats = [os.path.join(la, "Programs", "Python", "Python3*", "python.exe"),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Python3*", "python.exe"),
                r"C:\Python3*\python.exe"]
    else:
        pats = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3",
                "/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"]
    for pat in pats:
        for p in sorted(glob.glob(pat), reverse=True):
            add([p], "Standard install folder")
    return out


def probe(cmd: list[str], timeout: int = 12) -> dict | None:
    """Run the interpreter once. None when it does not start (missing, or the Store stub)."""
    try:
        r = subprocess.run(cmd + ["-c", _PROBE], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except Exception:
        return None
    lines = r.stdout.strip().splitlines()
    if r.returncode != 0 or len(lines) < 2:
        return None
    return {"executable": lines[0].strip(), "version": lines[1].strip(),
            "streamlit": lines[2].strip() if len(lines) > 2 else ""}


def current() -> dict:
    """The interpreter running this app (always correct, even when `python` is not on PATH)."""
    try:
        import streamlit
        ver = streamlit.__version__
    except Exception:
        ver = ""
    venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or bool(os.environ.get("CONDA_PREFIX"))
    return {"executable": sys.executable, "version": "%d.%d.%d" % sys.version_info[:3], "streamlit": ver,
            "environment": "virtual / Conda environment" if venv else "system installation"}


def discover() -> list[dict]:
    """Every candidate that really starts, with its Streamlit version (deduplicated by executable)."""
    seen, out = set(), []
    for label, cmd, source in candidates():
        info = probe(cmd)
        if not info:
            continue
        key = os.path.normcase(os.path.realpath(info["executable"]))
        if key in seen:
            continue
        seen.add(key)
        out.append({"command": label, "source": source, **info})
    return out


def quote(p: str) -> str:
    return f'"{p}"' if " " in p or any(ord(ch) > 127 for ch in p) else p


def launch_commands(port: int = 8501) -> list[tuple[str, str]]:
    """[(description, command)] that work even when `python` is not on PATH."""
    args = f"-m streamlit run app.py --server.address 0.0.0.0 --server.port {port}"
    cur = current()["executable"]
    cmds = []
    if IS_WIN:
        cmds.append(("Launcher (finds Python automatically) - Command Prompt or double-click", "run_lan.bat"))
        cmds.append(("Launcher - PowerShell", r"powershell -NoProfile -ExecutionPolicy Bypass -File .\run_lan.ps1"))
        cmds.append(("This exact interpreter - PowerShell", f"& {quote(cur)} {args}"))
        cmds.append(("This exact interpreter - Command Prompt", f"{quote(cur)} {args}"))
        if shutil.which("py"):
            cmds.append(("Windows Python launcher", f"py -3 {args}"))
    else:
        cmds.append(("Launcher (finds Python automatically)", "bash run_lan.sh"))
        cmds.append(("This exact interpreter", f"{quote(cur)} {args}"))
    return cmds
