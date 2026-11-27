"""Time-based "New" badges (no browser cookies).

A record or announcement is "New" when it was written or updated within the last NEW_DAYS days:
  * announcements      -> updated_at / created_at in announcements.json
  * quiz / attendance  -> updated_at column of grades_database.xlsx (set by grading and grade revisions)
  * workbook criteria  -> portal_updates.json (set when Homework/Experiment/Project/Midterm/Final scores are
                          written by grading or a grade revision)
  * No Class entries   -> created_at in class_status.csv
Older records without a timestamp are never marked new.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .common import is_blank, now, now_iso, read_json, write_json

NEW_DAYS = 7
UPDATES_FILE = "portal_updates.json"


def parse_ts(value):
    if value is None or is_blank(value):
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=now().tzinfo)
    return ts


def is_recent(value, days: int = NEW_DAYS) -> bool:
    ts = parse_ts(value)
    return bool(ts) and now() - ts <= timedelta(days=days)


def announcement_recent(a: dict, days: int = NEW_DAYS) -> bool:
    return is_recent(a.get("updated_at") or a.get("created_at"), days)


# ----------------------------------------------------------------------------- portal_updates.json
def load_updates() -> dict:
    """{"<COURSE>|<column>": iso, "<COURSE>|<column>|<student_id>": iso}"""
    return read_json(UPDATES_FILE, {}) or {}


def mark_updates(course: str, columns=(), student_columns=()) -> str:
    """Stamp whole columns (a graded item) and/or single student cells (a revision) with the current time."""
    data = load_updates()
    ts = now_iso()
    for col in columns:
        data[f"{course}|{col}"] = ts
    for col, sid in student_columns:
        data[f"{course}|{col}|{sid}"] = ts
    # keep the file small: drop stamps older than 60 days
    data = {k: v for k, v in data.items() if is_recent(v, 60)}
    return write_json(UPDATES_FILE, data, backup=False)


def item_updated(updates: dict, course: str, column: str, sid: str):
    """Latest timestamp for one student's item (student-level revision or column-level grading)."""
    vals = [parse_ts(updates.get(f"{course}|{column}|{sid}")), parse_ts(updates.get(f"{course}|{column}"))]
    vals = [v for v in vals if v]
    return max(vals) if vals else None
