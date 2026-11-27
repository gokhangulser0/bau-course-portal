"""Stamped backups and rollback.

Every write to a workbook, roster, config, announcement or status file goes through
`backup_file()` first. Backup names:

    backups/<TERM>_<COURSE_CODE>_<YYYYMMDD_HHMMSS>_<original_filename>

Global files (database, config, announcements, status) use the course code `GLOBAL`.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass

from .common import BACKUP_DIR, BASE_DIR, path, read_json, stamp, CONFIG_FILE

_NAME_RE = re.compile(r"^(?P<term>[^_]+)_(?P<course>[^_]+)_(?P<ts>\d{8}_\d{6})(?:-(?P<n>\d+))?_(?P<orig>.+)$")


@dataclass
class BackupEntry:
    name: str
    term: str
    course: str
    timestamp: str
    original: str
    size: int

    @property
    def pretty_time(self) -> str:
        t = self.timestamp
        return f"{t[0:4]}-{t[4:6]}-{t[6:8]} {t[9:11]}:{t[11:13]}:{t[13:15]}"


def _infer_term_course(filename: str) -> tuple[str, str]:
    cfg = read_json(CONFIG_FILE, {}) or {}
    term = cfg.get("active_term") or "NOTERM"
    course = "GLOBAL"
    name = os.path.basename(filename)
    for code, c in (cfg.get("courses") or {}).items():
        if name.upper().startswith(code.upper() + "_"):
            course, term = code, c.get("term") or term
            break
    else:
        m = re.match(r"([A-Za-z]{2,6}\d{3,5})_", name)
        if m:
            course = m.group(1).upper()
    return term, course


def backup_file(file_path: str, term: str | None = None, course: str | None = None) -> str | None:
    """Copy `file_path` into backups/ with a term/course/timestamp stamp. Returns the backup path."""
    src = file_path if os.path.isabs(file_path) else path(file_path)
    if not os.path.exists(src):
        return None
    t_inf, c_inf = _infer_term_course(src)
    term = (term or t_inf or "NOTERM").replace("_", "-")
    course = (course or c_inf or "GLOBAL").replace("_", "-")
    os.makedirs(path(BACKUP_DIR), exist_ok=True)
    base = f"{term}_{course}_{stamp()}"
    dst = path(BACKUP_DIR, f"{base}_{os.path.basename(src)}")
    n = 1
    while os.path.exists(dst):            # several writes within the same second
        n += 1
        dst = path(BACKUP_DIR, f"{base}-{n}_{os.path.basename(src)}")
    shutil.copy2(src, dst)
    _prune(os.path.basename(src))
    return dst


def _prune(original: str) -> None:
    keep = int(((read_json(CONFIG_FILE, {}) or {}).get("settings") or {}).get("backup_keep_per_file", 40))
    items = [b for b in list_backups() if b.original == original]
    for b in items[keep:]:
        try:
            os.remove(path(BACKUP_DIR, b.name))
        except OSError:
            pass


def list_backups(original: str | None = None) -> list[BackupEntry]:
    """Newest first."""
    d = path(BACKUP_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        m = _NAME_RE.match(name)
        if not m:
            continue
        if original and m.group("orig") != original:
            continue
        out.append(BackupEntry(name, m.group("term"), m.group("course"), m.group("ts") + (m.group("n") or ""),
                               m.group("orig"), os.path.getsize(os.path.join(d, name))))
    out.sort(key=lambda b: (b.timestamp, b.name), reverse=True)
    return out


def latest_backup() -> BackupEntry | None:
    items = list_backups()
    return items[0] if items else None


def restore(backup_name: str) -> tuple[str, str | None]:
    """Roll a file back to `backup_name`. The current version is backed up first.
    Returns (restored_path, safety_backup_path)."""
    m = _NAME_RE.match(backup_name)
    if not m:
        raise ValueError(f"Not a recognised backup file name: {backup_name}")
    src = path(BACKUP_DIR, backup_name)
    if not os.path.exists(src):
        raise FileNotFoundError(f"Backup not found: {backup_name}")
    target = path(m.group("orig"))
    safety = backup_file(target, term=m.group("term"), course=m.group("course"))
    shutil.copy2(src, target)
    return target, safety


def relative(p: str) -> str:
    try:
        return os.path.relpath(p, BASE_DIR)
    except ValueError:
        return p
