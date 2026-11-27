"""Targeted grade & feedback revision for one existing record (no Excel needed).

    items(course)                       every revisable item: Quiz #3, HW #2, Exp #1, Midterm, Final, ...
    students(course, item, section)     the students of that section for the chosen item
    current(course, item, sid)          current score and feedback
    save(course, item, sid, score, feedback, reason)   (feedback only for Quiz and Experiment items)
        -> stamped backups, writes the course workbook (template formatting untouched: only cell values
           change) and, for quizzes, the portal database; appends to revision_log.json.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

from . import config as cfgmod
from . import dbio
from . import excel_io as xio
from .common import DB_FILE, clean_id, clean_section, ensure_writable, is_blank, now_iso, path, read_json, \
    to_number, \
    touch_meta, write_json

LOG_FILE = "revision_log.json"


@dataclass
class Item:
    label: str          # "Quiz #3", "HW #2", "Midterm"
    criterion: str      # "Quiz", "Homework", ...
    kind: str           # "quiz" or "component"
    workbook: str
    column: str         # score column in the workbook
    quiz_no: int | None = None
    has_feedback: bool = True   # written feedback only for Quiz and Experiment / Lab Report

    @property
    def fb_column(self) -> str:
        return cfgmod.feedback_column(self.column)


def items(course: dict) -> list[Item]:
    out = []
    for comp in course["components"]:
        if comp["type"] == "quiz":
            for q in range(1, comp["count"] + 1):
                out.append(Item(f"Quiz #{q}", comp["label"], "quiz", course["workbooks"]["grades"], f"Quiz #{q}", q))
        elif comp["type"] in ("items", "single"):
            for col in cfgmod.item_columns(comp):
                out.append(Item(col, comp["label"], "component", comp["workbook"], col,
                                has_feedback=cfgmod.has_feedback(comp)))
    return out


def _workbook_students(item: Item) -> list[dict]:
    return xio.read_students(item.workbook) if os.path.exists(path(item.workbook)) else []


def students(course: dict, item: Item, section: str) -> list[dict]:
    """[{"Student ID", "Full Name", "Section"}] of one section for the chosen item."""
    rows = [r for r in _workbook_students(item) if r["Section"] == section]
    if rows:
        return sorted(rows, key=lambda r: r["Student ID"])
    if item.kind == "quiz":                       # web portal: grades workbook is local-only, use the database
        db = dbio.read_records()
        db = db[db["course_code"].astype(str).str.strip() == course["code"]]
        db = db[db["section"].map(clean_section) == section]
        seen, out = set(), []
        for _, r in db.iterrows():
            sid = clean_id(r["student_id"])
            if sid and sid not in seen:
                seen.add(sid)
                out.append({"Student ID": sid, "Full Name": str(r["full_name"]), "Section": section})
        return sorted(out, key=lambda r: r["Student ID"])
    return []


def _db_rows(course: dict, item: Item, sid: str, db: pd.DataFrame):
    m = ((db["course_code"].astype(str).str.strip() == course["code"]) & (db["student_id"].map(clean_id) == sid)
         & (db["quiz_no"].astype(str).str.strip() == f"Quiz {item.quiz_no}"))
    return m


def current(course: dict, item: Item, sid: str) -> dict:
    """{"score", "feedback", "source"} - for quizzes grades_database.xlsx (the source of truth) wins over the
    workbook; other items are read from their workbook."""
    score, fb, source = None, "", ""
    t = xio.read_table(item.workbook) if os.path.exists(path(item.workbook)) else pd.DataFrame()
    if not t.empty and "Student ID" in t.columns:
        r = t[t["Student ID"].map(clean_id) == sid]
        if not r.empty:
            row = r.iloc[0]
            score = to_number(row.get(item.column))
            if item.has_feedback:
                fb = "" if is_blank(row.get(item.fb_column)) else str(row.get(item.fb_column))
            source = item.workbook
    if item.kind == "quiz":
        db = dbio.read_records()
        m = _db_rows(course, item, sid, db)
        if m.any():
            r = db[m].iloc[-1]
            score = to_number(r["quiz_score"])
            fb = "" if is_blank(r["feedback"]) else str(r["feedback"])
            source = DB_FILE
    return {"score": score, "feedback": fb, "source": source}


def save(course: dict, item: Item, sid: str, score, feedback: str, reason: str = "") -> dict:
    """Write the revision. Returns {"files": [changed files], "log": [English lines]}."""
    term, code = course.get("term"), course["code"]
    ensure_writable(item.workbook, *([DB_FILE] if item.kind == "quiz" else []))
    if not item.has_feedback:
        feedback = ""                          # Homework, Midterm, Final, Project: score only
    before = current(course, item, sid)
    files, log = [], []
    val = "" if score is None else float(score)
    if os.path.exists(path(item.workbook)):
        cols = {item.column: {sid: val}}
        if item.has_feedback:
            cols[item.fb_column] = {sid: feedback}
        r1 = xio.write_many(item.workbook, cols, term=term, course=code)
        what_ = f"{item.column} and feedback" if item.has_feedback else item.column
        if r1["students"]:
            files.append(item.workbook)
            log.append(f"{item.workbook}: {what_} updated on sheet(s) {', '.join(sorted(set(r1['sheets'])))}.")
        elif before["source"] == item.workbook:
            log.append(f"{item.workbook}: values were already up to date.")
        if item.kind == "component" and r1["students"]:
            from . import badges
            badges.mark_updates(code, student_columns=[(item.column, sid)])
            files.append(badges.UPDATES_FILE)
    elif item.kind == "component":
        raise ValueError(f"{item.workbook} does not exist. Create it on Courses & Criteria (Workbooks) first.")
    if item.kind == "quiz":
        db = dbio.read_records()
        for c in ("quiz_score", "feedback"):
            db[c] = db[c].astype(object)
        m = _db_rows(course, item, sid, db)
        if m.any():
            db.loc[m, "quiz_score"] = val if val != "" else None
            db.loc[m, "feedback"] = feedback
            db["updated_at"] = db["updated_at"].astype(object)
            db.loc[m, "updated_at"] = now_iso()
            dbio.write(records=db, term=term)
            touch_meta()
            files.append(DB_FILE)
            log.append(f"{DB_FILE}: Quiz {item.quiz_no} record updated.")
        else:
            log.append(f"{DB_FILE}: no portal record exists yet for Quiz {item.quiz_no}; only the workbook was "
                       f"changed. Grade or publish that quiz week first if the student should see it.")
    entry = {"at": now_iso(), "course": code, "item": item.label, "student_id": sid,
             "old_score": before["score"], "new_score": None if val == "" else val,
             "old_feedback": before["feedback"], "new_feedback": feedback, "reason": reason.strip(),
             "files": files}
    hist = read_json(LOG_FILE, [])
    hist.append(entry)
    write_json(LOG_FILE, hist, backup=False)
    return {"files": files, "log": log, "entry": entry}


def history(course_code: str, limit: int = 20) -> list[dict]:
    return [e for e in reversed(read_json(LOG_FILE, [])) if e.get("course") == course_code][:limit]
