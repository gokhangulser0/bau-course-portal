"""Sync the course workbooks FROM grades_database.xlsx (the database is the source of truth).

Manual edits are made in grades_database.xlsx, so whenever a course workbook and the database disagree the
workbook is updated, never the database:

    <COURSE>_grades.xlsx       Quiz #n      <- database quiz_score   (Quiz #n FB <- database feedback)
    <COURSE>_attendance.xlsx   Week #n      <- database attendance   ('+' if present in any record that week)

Rules
  * Only cell values change: every section sheet and All Sections keep their template formatting
    (row heights, column widths, borders, fonts, number formats, alignment). All Sections cells that are
    formulas pointing at a section sheet are left alone; they follow the section sheet.
  * A workbook value the database does not have is cleared only when the database holds that quiz / week
    for the student's section (so an item that was never written to the database is never wiped). This can be
    switched off with clear_missing=False.
  * Feedback is synced for quizzes only (Experiment feedback lives in its own workbook, not in the database;
    Homework, Midterm and Final have no feedback).
  * Students who are in the database but not in the workbook cannot be written; they are reported.

    plan(cfg, clear_missing=True) -> {"changes": [...], "skipped": [...]}     (nothing is written)
    apply(cfg, changes)           -> open-Excel check, stamped backups, value-only writes
"""
from __future__ import annotations

import os
import re
from collections import defaultdict

import pandas as pd

from . import config as cfgmod
from . import dbio
from . import excel_io as xio
from .common import clean_id, clean_section, ensure_writable, is_blank, path, to_number


def _text(v) -> str:
    return "" if is_blank(v) else re.sub(r"\s+", " ", str(v)).strip()


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.extract(r"(\d+)")[0], errors="coerce")


def _fmt(v) -> str:
    if v is None or v == "":
        return "empty"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v)
    return s if len(s) <= 40 else s[:39] + "…"


def _prepare(db: pd.DataFrame, code: str) -> pd.DataFrame:
    d = db.copy()
    for c in dbio.RECORD_COLUMNS:
        if c not in d.columns:
            d[c] = ""
    d = d[d["course_code"].astype(str).str.strip() == code].copy()
    d["_sid"] = d["student_id"].map(clean_id)
    d["_sec"] = d["section"].map(clean_section)
    d["_wn"] = _num(d["week"])
    qn = d["quiz_no"].astype(str).str.strip()
    d["_qn"] = _num(qn.where(qn.str.startswith("Quiz"), ""))
    d["_score"] = pd.to_numeric(d["quiz_score"], errors="coerce")
    return d


def plan_course(course: dict, db: pd.DataFrame, clear_missing: bool = True) -> dict:
    """{"changes": [{"Course", "File", "Section", "Item", "Student ID", "Workbook", "Database", "Action",
    _file, _col, _value}], "skipped": [{"Course", "Item", "Student ID", "Reason"}]}"""
    code = course["code"]
    d = _prepare(db, code)
    changes, skipped = [], []

    def add(file_rel, sec, item, sid, cur, new, action, col):
        changes.append({"Course": code, "File": file_rel, "Section": sec, "Item": item, "Student ID": sid,
                        "Workbook": _fmt(cur), "Database": _fmt(new), "Action": action,
                        "_file": file_rel, "_col": col, "_value": new})

    # ---------------------------------------------------------------- quizzes -> grades workbook
    gr = course["workbooks"]["grades"]
    qc = cfgmod.quiz_component(course)
    if qc and os.path.exists(path(gr)):
        t = xio.read_table(gr)
        if not t.empty:
            t = t.assign(_sid=t["Student ID"].map(clean_id)).drop_duplicates("_sid").set_index("_sid")
            in_wb = set(t.index)
            for q in range(1, qc["count"] + 1):
                col, fbc = f"Quiz #{q}", f"Quiz #{q} FB"
                dq = d[d["_qn"] == q].drop_duplicates("_sid", keep="last").set_index("_sid")
                held_secs = set(dq["_sec"])               # sections whose Quiz q is in the database
                if col not in t.columns:
                    if not dq.empty:
                        skipped.append({"Course": code, "Item": col, "Student ID": "—",
                                        "Reason": f"{gr} has no {col} column"})
                    continue
                for sid in sorted(in_wb | set(dq.index)):
                    if sid not in in_wb:
                        if sid in dq.index and not pd.isna(dq.at[sid, "_score"]):
                            skipped.append({"Course": code, "Item": col, "Student ID": sid,
                                            "Reason": f"Not listed in {gr} (run the roster sync first)"})
                        continue
                    sec = str(t.at[sid, "Section"]) if "Section" in t.columns else ""
                    xv = to_number(t.at[sid, col])
                    xf = _text(t.at[sid, fbc]) if fbc in t.columns else ""
                    if sid in dq.index:
                        dv = None if pd.isna(dq.at[sid, "_score"]) else float(dq.at[sid, "_score"])
                        dfb = _text(dq.at[sid, "feedback"])
                    elif clear_missing and sec in held_secs:
                        dv, dfb = None, ""
                    else:
                        continue                          # quiz not in the database for this section
                    if dv is not None and (xv is None or abs(xv - dv) > 1e-9):
                        add(gr, sec, col, sid, xv, dv, "Update score" if xv is not None else "Fill score", col)
                    elif dv is None and xv is not None and (sid in dq.index or clear_missing):
                        add(gr, sec, col, sid, xv, None, "Clear score (empty in the database)", col)
                    if fbc in t.columns and xf != dfb:
                        add(gr, sec, f"{col} feedback", sid, xf, dfb or None,
                            "Update feedback" if dfb else "Clear feedback", fbc)

    # ---------------------------------------------------------------- attendance workbook
    at = course["workbooks"]["attendance"]
    if os.path.exists(path(at)):
        t = xio.read_table(at)
        if not t.empty:
            t = t.assign(_sid=t["Student ID"].map(clean_id)).drop_duplicates("_sid").set_index("_sid")
            dd = d[d["_wn"].notna()]
            att = dd.assign(_p=dd["attendance"].astype(str).str.strip().eq("+"),
                            _a=dd["attendance"].astype(str).str.strip().eq("-"))
            agg = att.groupby(["_sid", "_wn"]).agg(p=("_p", "any"), a=("_a", "any"))
            weeks_by_sec = defaultdict(set)
            for (sec, w) in zip(dd["_sec"], dd["_wn"]):
                weeks_by_sec[sec].add(int(w))
            for w in range(1, course["weeks"] + 1):
                col = f"Week #{w}"
                if col not in t.columns:
                    continue
                for sid in t.index:
                    sec = str(t.at[sid, "Section"]) if "Section" in t.columns else ""
                    xv = _text(t.at[sid, col])
                    xm = xv if xv in ("+", "-") else ("" if not xv else xv)
                    if (sid, float(w)) in agg.index:
                        r = agg.loc[(sid, float(w))]
                        dm = "+" if r["p"] else ("-" if r["a"] else "")
                    elif clear_missing and w in weeks_by_sec.get(sec, set()):
                        dm = ""
                    else:
                        continue
                    if dm and xm != dm:
                        add(at, sec, col, sid, xm or None, dm, "Update attendance" if xm else "Fill attendance", col)
                    elif not dm and xm in ("+", "-"):
                        add(at, sec, col, sid, xm, None, "Clear attendance (no database record)", col)
    return {"changes": changes, "skipped": skipped}


def plan(cfg: dict, clear_missing: bool = True, db: pd.DataFrame | None = None) -> dict:
    db = dbio.read_records() if db is None else db
    out = {"changes": [], "skipped": []}
    if db.empty:
        return out
    for code in cfgmod.all_courses(cfg):
        r = plan_course(cfg["courses"][code], db, clear_missing)
        out["changes"] += r["changes"]
        out["skipped"] += r["skipped"]
    return out


def summarize(changes: list[dict]) -> list[dict]:
    c = defaultdict(int)
    for x in changes:
        c[(x["Course"], x["File"], x["Action"])] += 1
    return [{"Course": k[0], "File": k[1], "Change": k[2], "Cells": n} for k, n in sorted(c.items())]


def public(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in x.items() if not k.startswith("_")} for x in rows]


def files_of(changes: list[dict]) -> list[str]:
    return sorted({x["_file"] for x in changes})


def apply(cfg: dict, changes: list[dict] | None = None) -> dict:
    """Write the planned values into the course workbooks. grades_database.xlsx is only read.
    Returns {"cells", "files", "log"}."""
    changes = plan(cfg)["changes"] if changes is None else changes
    result = {"cells": 0, "files": [], "log": []}
    if not changes:
        result["log"].append("The course workbooks already match grades_database.xlsx. Nothing was changed.")
        return result
    files = files_of(changes)
    ensure_writable(*files)                      # nothing is written if any workbook is open in Excel
    by_file: dict = defaultdict(lambda: defaultdict(dict))
    course_of = {}
    for x in changes:
        by_file[x["_file"]][x["_col"]][x["Student ID"]] = x["_value"]
        course_of[x["_file"]] = x["Course"]
    for f, cols in by_file.items():
        code = course_of[f]
        term = (cfgmod.course(cfg, code) or {}).get("term")
        r = xio.write_many(f, cols, create_column=False, term=term, course=code)   # stamped backup first
        result["cells"] += r["cells"]
        result["files"].append(f)
        result["log"].append(f"{f}: {r['cells']} cell(s) updated on sheet(s) {', '.join(r['sheets']) or '—'}.")
    return result
