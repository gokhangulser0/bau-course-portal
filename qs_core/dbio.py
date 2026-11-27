"""Read/write grades_database.xlsx without losing any of its sheets.

Sheet 1 ("Sheet1")   weekly portal records (attendance, quizzes, feedback) - unchanged layout
Sheet "Roster"       current class roster per course (written by the semester roster import), so the
                     student portal recognises students of a new course before the first week is graded.
"""
from __future__ import annotations

import os

import pandas as pd

from . import backup as bk
from .common import DB_FILE, clean_id, clean_section, ensure_writable, path

RECORD_COLUMNS = ["student_id", "full_name", "course_code", "section", "week", "attendance", "quiz_no",
                  "quiz_score", "feedback", "attended_section", "updated_at"]
ROSTER_SHEET = "Roster"
ROSTER_COLUMNS = ["student_id", "full_name", "course_code", "course_title", "section", "department", "term",
                  "updated_at"]


def _p(p=None):
    return p or path(DB_FILE)


def _sheet_names(p) -> list[str]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(p, read_only=True)
        names = list(wb.sheetnames)
        wb.close()
        return names
    except Exception:
        return []


def records_sheet_name(p=None) -> str:
    names = _sheet_names(_p(p))
    first = next((n for n in names if n != ROSTER_SHEET), None)
    return first or "Sheet1"


def read_records(p=None) -> pd.DataFrame:
    p = _p(p)
    if not os.path.exists(p):
        return pd.DataFrame(columns=RECORD_COLUMNS)
    df = pd.read_excel(p, sheet_name=records_sheet_name(p), dtype={"student_id": str})
    for c in RECORD_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df


def read_roster(p=None) -> pd.DataFrame:
    p = _p(p)
    if not os.path.exists(p) or ROSTER_SHEET not in _sheet_names(p):
        return pd.DataFrame(columns=ROSTER_COLUMNS)
    df = pd.read_excel(p, sheet_name=ROSTER_SHEET, dtype=str).fillna("")
    for c in ROSTER_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df["student_id"] = df["student_id"].map(clean_id)
    df["section"] = df["section"].map(clean_section)
    df["course_code"] = df["course_code"].astype(str).str.strip()
    return df[ROSTER_COLUMNS]


def write(p=None, records: pd.DataFrame | None = None, roster: pd.DataFrame | None = None,
          backup: bool = True, term: str | None = None) -> str:
    """Write the records and/or roster sheet; the sheet that is not passed is kept as it is."""
    p = _p(p)
    ensure_writable(p)
    if records is None:
        records = read_records(p)
    if roster is None:
        roster = read_roster(p)
    if backup and os.path.exists(p):
        bk.backup_file(p, term=term, course="GLOBAL")
    sheet = records_sheet_name(p) if os.path.exists(p) else "Sheet1"
    tmp = p + ".tmp.xlsx"
    with pd.ExcelWriter(tmp, engine="openpyxl") as xw:
        records.to_excel(xw, sheet_name=sheet, index=False)
        if roster is not None and not roster.empty:
            roster[ROSTER_COLUMNS].to_excel(xw, sheet_name=ROSTER_SHEET, index=False)
    os.replace(tmp, p)
    return p


def replace_course_roster(roster_all: pd.DataFrame, code: str, rows: list[dict]) -> pd.DataFrame:
    keep = roster_all[roster_all["course_code"] != code]
    new = pd.DataFrame(rows, columns=ROSTER_COLUMNS)
    out = pd.concat([keep, new], ignore_index=True) if len(keep) else new
    return out.sort_values(["course_code", "section", "student_id"]).reset_index(drop=True)
