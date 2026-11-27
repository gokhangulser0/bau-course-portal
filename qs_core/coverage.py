"""Class-wide missing entries summary, at course / assessment-column level only (no per-student detail).

For every configured criterion item (Quiz 1..N, HW #1..#N, Exp #1..#N, Project, Midterm, Final):
    entered   number of students with a numeric score
    missing   enrolled students without a score
    coverage  entered / enrolled
Items nobody has a score for yet are reported as "Not started" instead of "missing".
"""
from __future__ import annotations

import pandas as pd

from . import config as cfgmod
from .common import clean_id


def enrolled_ids(code: str, db: pd.DataFrame, tables: dict, roster: pd.DataFrame | None = None) -> set:
    ids = set()
    if db is not None and not db.empty:
        ids |= set(db.loc[db["course_code"] == code, "student_id"])
    if roster is not None and not roster.empty:
        ids |= set(roster.loc[roster["course_code"] == code, "student_id"])
    for t in tables.values():
        if t is not None and not t.empty and "Student ID" in t.columns:
            ids |= set(t["Student ID"].map(clean_id))
    return {i for i in ids if len(i) >= 5}


def course_coverage(course: dict, db: pd.DataFrame, tables: dict, roster: pd.DataFrame | None = None) -> pd.DataFrame:
    code = course["code"]
    enrolled = enrolled_ids(code, db, tables, roster)
    n = len(enrolled)
    rows = []

    def add(assessment, item, entered, base):
        missing = max(0, base - entered)
        if entered == 0:
            status = "Not started"
        elif missing == 0:
            status = "Complete"
        else:
            status = "Missing entries"
        rows.append({"Assessment": assessment, "Item": item, "Entered": int(entered),
                     "Missing": int(missing) if entered else 0, "Enrolled": int(base),
                     "Coverage": round(100.0 * entered / base, 1) if base else 0.0, "Status": status})

    for comp in course["components"]:
        label = comp["label"]
        if comp["type"] == "quiz":
            cdb = db[(db["course_code"] == code)] if db is not None and not db.empty else pd.DataFrame()
            for q in range(1, comp["count"] + 1):
                if cdb.empty:
                    add(label, f"Quiz {q}", 0, n)
                    continue
                qrows = cdb[(cdb["quiz_n"] == q)]
                scored = set(qrows.loc[qrows["quiz_score_num"].notna(), "student_id"])
                add(label, f"Quiz {q}", len(scored & enrolled) if enrolled else len(scored), n)
        elif comp["type"] in ("items", "single"):
            t = tables.get(comp["workbook"])
            base = len(t) if t is not None and not t.empty else n
            for col in cfgmod.item_columns(comp):
                if t is None or t.empty or col not in t.columns:
                    add(label, col, 0, base)
                    continue
                entered = int(pd.to_numeric(t[col], errors="coerce").notna().sum())
                add(label, col, entered, base)
    return pd.DataFrame(rows, columns=["Assessment", "Item", "Entered", "Missing", "Enrolled", "Coverage", "Status"])
