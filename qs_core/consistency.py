"""Excel vs. portal database consistency check.

Quiz scores and feedback are stored in <COURSE>_grades.xlsx and in grades_database.xlsx; attendance in
<COURSE>_attendance.xlsx and in grades_database.xlsx. The app always updates both, but an edit made by hand
in Excel (or in the database) makes them drift apart. This module lists the records that differ.
grades_database.xlsx is the source of truth: qs_core.db_sync updates the workbooks from it.
"""
from __future__ import annotations

import os
import re

import pandas as pd

from . import config as cfgmod
from . import dbio
from . import excel_io as xio
from .common import clean_id, is_blank, path, to_number


def _norm_text(v) -> str:
    return "" if is_blank(v) else re.sub(r"\s+", " ", str(v)).strip()


def check_course(course: dict, db: pd.DataFrame | None = None) -> list[dict]:
    """[{"Course", "Item", "Student ID", "Excel", "Database", "Problem"}]"""
    code = course["code"]
    db = dbio.read_records() if db is None else db
    if db.empty:
        return []
    d = db[db["course_code"].astype(str).str.strip() == code].copy()
    d["sid"] = d["student_id"].map(clean_id)
    d["qn"] = pd.to_numeric(d["quiz_no"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    d["wn"] = pd.to_numeric(d["week"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    d["score"] = pd.to_numeric(d["quiz_score"], errors="coerce")
    out = []

    def add(item, sid, xv, dv, problem):
        out.append({"Course": code, "Item": item, "Student ID": sid, "Excel": xv, "Database": dv,
                    "Problem": problem})

    # quizzes: grades workbook vs database
    gr = course["workbooks"]["grades"]
    qc = cfgmod.quiz_component(course)
    if qc and os.path.exists(path(gr)):
        t = xio.read_table(gr)
        if not t.empty:
            t = t.set_index(t["Student ID"].map(clean_id))
            for q in range(1, qc["count"] + 1):
                col, fbc = f"Quiz #{q}", f"Quiz #{q} FB"
                if col not in t.columns and not (d["qn"] == q).any():
                    continue
                dq = d[d["qn"] == q].drop_duplicates("sid", keep="last").set_index("sid")
                for sid in set(t.index) | set(dq.index):
                    xs = to_number(t.at[sid, col]) if (sid in t.index and col in t.columns) else None
                    ds = dq.at[sid, "score"] if sid in dq.index else None
                    ds = None if ds is None or pd.isna(ds) else float(ds)
                    if xs is None and ds is None:
                        continue
                    if xs is not None and ds is not None and abs(xs - ds) > 0.01:
                        add(col, sid, xs, ds, "Score differs")
                    elif xs is not None and ds is None:
                        add(col, sid, xs, "—", "Only in Excel")
                    elif xs is None and ds is not None:
                        add(col, sid, "—", ds, "Only in database")
                    elif fbc in t.columns and sid in dq.index and sid in t.index:
                        xf, df_ = _norm_text(t.at[sid, fbc]), _norm_text(dq.at[sid, "feedback"])
                        if xf and df_ and xf != df_:
                            add(f"{col} feedback", sid, xf[:40] + ("…" if len(xf) > 40 else ""),
                                df_[:40] + ("…" if len(df_) > 40 else ""), "Feedback differs")

    # attendance: attendance workbook vs database
    at = course["workbooks"]["attendance"]
    if os.path.exists(path(at)):
        t = xio.read_table(at)
        if not t.empty:
            t = t.set_index(t["Student ID"].map(clean_id))
            wk = d.assign(p=d["attendance"].astype(str).str.strip().eq("+")).groupby(["sid", "wn"])["p"].any()
            for w in range(1, course["weeks"] + 1):
                col = f"Week #{w}"
                if col not in t.columns:
                    continue
                for sid in t.index:
                    xm = _norm_text(t.at[sid, col])
                    xm = xm if xm in ("+", "-") else ""
                    dm = ("+" if wk[(sid, w)] else "-") if (sid, w) in wk.index else ""
                    if xm and dm and xm != dm:
                        add(col, sid, xm, dm, "Attendance differs")
                    elif xm == "+" and not dm:
                        add(col, sid, xm, "—", "Only in Excel")
                    elif dm == "+" and not xm:
                        add(col, sid, "—", dm, "Only in database")
    return out


def check_all(cfg: dict) -> list[dict]:
    db = dbio.read_records()
    rows = []
    for code in cfgmod.all_courses(cfg):
        try:
            rows += check_course(cfg["courses"][code], db)
        except Exception as e:  # noqa: BLE001
            rows.append({"Course": code, "Item": "—", "Student ID": "—", "Excel": "—", "Database": "—",
                         "Problem": f"Check failed: {e}"})
    return rows
