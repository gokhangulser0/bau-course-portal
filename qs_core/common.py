"""Shared paths, time helpers and value-cleaning utilities."""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOCAL_TZ = "Europe/Istanbul"

# Global data files (relative to BASE_DIR)
CONFIG_FILE = "courses_config.json"
DB_FILE = "grades_database.xlsx"
STATUS_FILE = "class_status.csv"
META_FILE = "portal_meta.json"
ANNOUNCEMENTS_FILE = "announcements.json"
LEDGER_FILE = "billing_ledger.json"
RUBRIC_FILE = "rubric.txt"

BACKUP_DIR = "backups"
ARCHIVE_DIR = "archives"
REVIEW_DIR = "review_queue"
TEMPLATE_DIR = "templates"
INPUT_DIR = "input"


def path(*parts: str) -> str:
    """Absolute path inside the workspace."""
    return os.path.join(BASE_DIR, *parts)


def zone(name: str = LOCAL_TZ):
    """Time zone object. Falls back to a fixed UTC+3 offset when tzdata is missing (Windows)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=3))


def now() -> datetime:
    return datetime.now(zone())


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def stamp() -> str:
    return now().strftime("%Y%m%d_%H%M%S")


def is_blank(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    try:
        import pandas as pd
        if val is pd.NA or val is pd.NaT:
            return True
    except Exception:
        pass
    return str(val).strip().lower() in ("", "nan", "none", "<na>")


def clean_id(val) -> str:
    """Normalise a student ID to a digit string. 2202739.0 -> '2202739'; keeps leading zeros of text IDs."""
    if is_blank(val):
        return ""
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    s = str(val).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return re.sub(r"\D", "", s)


def clean_section(val) -> str:
    if is_blank(val):
        return ""
    s = str(val).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s


def to_number(val):
    """Float value of a score cell, or None for blank / 'Pending' / text."""
    if is_blank(val):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return s or "item"


def read_json(rel_or_abs: str, default):
    p = rel_or_abs if os.path.isabs(rel_or_abs) else path(rel_or_abs)
    if not os.path.exists(p):
        return default
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(rel_or_abs: str, data, backup: bool = True, term: str | None = None,
               course: str | None = None) -> str:
    """Write JSON atomically, creating a stamped backup of the previous version first."""
    p = rel_or_abs if os.path.isabs(rel_or_abs) else path(rel_or_abs)
    if backup and os.path.exists(p):
        from . import backup as _bk
        _bk.backup_file(p, term=term, course=course)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return p


def touch_meta() -> None:
    """Record the 'Data last updated' timestamp shown on the student portal."""
    write_json(META_FILE, {"db_updated": now_iso()}, backup=False)


# ----------------------------------------------------------------------------- open-file (Excel lock) check
class FileLockedError(ValueError):
    """Raised before writing when a workbook is open in Excel (prevents a half-written update)."""


def is_locked(rel_or_abs: str) -> bool:
    """True when Excel (or another program) holds the file: an Office lock file '~$name' exists next to it,
    or the file cannot be opened for writing."""
    p = rel_or_abs if os.path.isabs(rel_or_abs) else path(rel_or_abs)
    lock = os.path.join(os.path.dirname(p), f"~${os.path.basename(p)}")
    if os.path.exists(lock):
        return True
    if not os.path.exists(p):
        return False
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def ensure_writable(*files) -> None:
    """Check every file first; raise one clear English message listing all files that are open."""
    locked = [os.path.basename(f) for f in files if f and is_locked(f)]
    if locked:
        names = ", ".join(locked)
        raise FileLockedError(f"{names} {'is' if len(locked) == 1 else 'are'} open in Excel. Save and close "
                              f"{'it' if len(locked) == 1 else 'them'}, then try again. Nothing was changed.")
