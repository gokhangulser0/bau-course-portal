"""Absent students receive 0 for weekly in-class assessments (Quiz and Experiment / Lab Report).

A record qualifies when all of these hold:
  * the assessment was held for the student's section (its week is known from the portal database or from
    an applied grading run),
  * the student is marked absent for that week (attendance workbook or portal database) and is not marked
    present anywhere that week (a student who attended another section's session is present),
  * the student has no real score for it: the score cell is empty (or already 0) in the course workbook and
    in the portal database.

A real score (e.g. a make-up quiz) is never overwritten. Homework, Midterm, Final and Project are never
auto-zeroed and never get feedback.

    plan(cfg)            -> list of planned changes (nothing is written)
    apply(cfg, changes)  -> lock check, stamped backups, writes the workbooks and grades_database.xlsx
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

import pandas as pd

from . import config as cfgmod
from . import dbio
from . import excel_io as xio
from .common import DB_FILE, REVIEW_DIR, clean_id, clean_section, ensure_writable, is_blank, now_iso, path, \
    read_json, to_number, touch_meta

ABSENT_NOTE = "Absent - 0 points."
# Old placeholder texts that may be replaced by the absent note
PLACEHOLDER_FB = {"", "-", "absent", "absent.", "absent from class.", "absent - 0 points", "absent - 0 points."}
_ABSENT = {"-", "a", "absent", "0", "x", "no", "n"}
_PRESENT = {"+", "p", "present", "1", "yes", "y"}


def mark(v) -> str:
    """'+' (present), '-' (absent) or '' (unknown) for an attendance cell or database value."""
    if is_blank(v):
        return ""
    s = str(v).strip().lower()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    if s in _PRESENT:
        return "+"
    if s in _ABSENT:
        return "-"
    return ""


def is_placeholder_fb(v) -> bool:
    return is_blank(v) or str(v).strip().lower() in PLACEHOLDER_FB


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.extract(r"(\d+)")[0], errors="coerce")


def prepare_db(db: pd.DataFrame) -> pd.DataFrame:
    """Database records with helper columns (_sid, _sec, _wn, _qn, _score)."""
    d = db.copy()
    for c in dbio.RECORD_COLUMNS:
        if c not in d.columns:
            d[c] = ""
    d["_sid"] = d["student_id"].map(clean_id)
    d["_sec"] = d["section"].map(clean_section)
    d["_course"] = d["course_code"].astype(str).str.strip()
    d["_wn"] = _num(d["week"])
    d["_qn"] = _num(d["quiz_no"].where(d["quiz_no"].astype(str).str.strip().str.startswith("Quiz"), ""))
    d["_score"] = pd.to_numeric(d["quiz_score"], errors="coerce")
    return d


# ----------------------------------------------------------------------------- inputs
def attendance_marks(course: dict, d: pd.DataFrame | None = None) -> dict:
    """{(student_id, week): '+' | '-'} from the attendance workbook and the portal database.
    Present anywhere that week wins over absent."""
    code = course["code"]
    out: dict = {}

    def put(sid, w, m):
        if sid and m and w:
            if out.get((sid, w)) != "+":
                out[(sid, w)] = m

    at = course["workbooks"]["attendance"]
    if os.path.exists(path(at)):
        t = xio.read_table(at)
        if not t.empty:
            for w in range(1, course["weeks"] + 1):
                col = f"Week #{w}"
                if col in t.columns:
                    for sid, v in zip(t["Student ID"].map(clean_id), t[col]):
                        put(sid, w, mark(v))
    if d is not None and not d.empty:
        dc = d[d["_course"] == code]
        for sid, w, v in zip(dc["_sid"], dc["_wn"], dc["attendance"]):
            if not pd.isna(w):
                put(sid, int(w), mark(v))
    return out


def held_weeks(course: dict, d: pd.DataFrame | None = None) -> dict:
    """{(criterion, number, section): week} for the Quiz / Experiment items already held.
    Quizzes: from the portal database. Both: from applied grading runs in review_queue/."""
    code = course["code"]
    votes: dict = defaultdict(Counter)
    if d is not None and not d.empty:
        dc = d[(d["_course"] == code) & d["_qn"].notna() & d["_wn"].notna()]
        for (q, sec), g in dc.groupby(["_qn", "_sec"]):
            for w, n in g["_wn"].value_counts().items():
                votes[("Quiz", int(q), sec)][int(w)] += int(n)
    qdir = path(REVIEW_DIR)
    if os.path.isdir(qdir):
        for name in os.listdir(qdir):
            if not name.endswith(".json"):
                continue
            q = read_json(os.path.join(REVIEW_DIR, name), {})
            if q.get("course") != code or q.get("status") != "applied" or not q.get("week"):
                continue
            target = q.get("target", "quiz")
            crit = "Quiz" if target == "quiz" else ("Experiment" if target == "experiment" else None)
            if not crit or not q.get("quiz"):
                continue
            for sec in [x.strip() for x in str(q.get("section", "")).replace(";", ",").split(",") if x.strip()]:
                votes[(crit, int(q["quiz"]), sec)][int(q["week"])] += 1000      # a grading run is authoritative
    return {k: v.most_common(1)[0][0] for k, v in votes.items()}


def assessments(course: dict) -> list[dict]:
    """Auto-zero items of a course: [{"criterion", "number", "column", "fb_column", "workbook", "db"}]."""
    out = []
    qc = cfgmod.quiz_component(course)
    if qc:
        for q in range(1, qc["count"] + 1):
            out.append({"criterion": "Quiz", "number": q, "column": f"Quiz #{q}",
                        "fb_column": f"Quiz #{q} FB", "workbook": course["workbooks"]["grades"], "db": True})
    ec = cfgmod.experiment_component(course)
    if ec and cfgmod.absent_zero(ec) and ec["type"] == "items":
        for n in range(1, ec["count"] + 1):
            col = f"{ec['column_prefix']}{n}"
            out.append({"criterion": "Experiment", "number": n, "column": col,
                        "fb_column": cfgmod.feedback_column(col) if cfgmod.has_feedback(ec) else None,
                        "workbook": ec["workbook"], "db": False})
    return out


# ----------------------------------------------------------------------------- plan
def plan_course(course: dict, d: pd.DataFrame, marks: dict | None = None) -> list[dict]:
    code = course["code"]
    marks = attendance_marks(course, d) if marks is None else marks
    held = held_weeks(course, d)
    dc = d[d["_course"] == code]
    tables: dict = {}
    out = []
    for it in assessments(course):
        wb = it["workbook"]
        if not os.path.exists(path(wb)):
            continue
        if wb not in tables:
            tables[wb] = xio.read_table(wb)
        t = tables[wb]
        if t.empty or it["column"] not in t.columns:
            continue
        t = t.assign(_sid=t["Student ID"].map(clean_id))
        for sec in course.get("sections") or sorted(t.get("Section", pd.Series(dtype=str)).dropna().unique()):
            week = held.get((it["criterion"], it["number"], str(sec)))
            if not week:
                continue                        # not held for this section yet (or week unknown)
            ts = t[t["Section"].astype(str) == str(sec)] if "Section" in t.columns else t.iloc[0:0]
            for _, r in ts.iterrows():
                sid = r["_sid"]
                if marks.get((sid, week)) != "-":
                    continue
                xs = to_number(r.get(it["column"]))
                xf = r.get(it["fb_column"]) if it["fb_column"] else None
                ds, df_, row_idx = None, None, None
                if it["db"]:
                    m = dc[(dc["_sid"] == sid) & (dc["_wn"] == week) & (dc["_sec"] == str(sec))]
                    mq = m[m["_qn"] == it["number"]]
                    if mq.empty:
                        mq = m[m["quiz_no"].astype(str).str.strip().isin(["No Quiz", "nan", "", "None"])]
                    if not mq.empty:
                        row_idx = int(mq.index[0])
                        ds = None if pd.isna(mq.iloc[0]["_score"]) else float(mq.iloc[0]["_score"])
                        df_ = mq.iloc[0]["feedback"]
                    else:   # the quiz may be stored under another week label: never duplicate it
                        other = dc[(dc["_sid"] == sid) & (dc["_qn"] == it["number"])]
                        if not other.empty:
                            continue
                if (xs is not None and xs != 0) or (ds is not None and ds != 0):
                    continue                    # a real score exists somewhere: keep it
                fix_x = xs is None
                fix_d = it["db"] and ds is None
                fb_x = bool(it["fb_column"]) and (fix_x or fix_d) and is_placeholder_fb(xf) \
                    and str(xf or "").strip() != ABSENT_NOTE
                fb_d = it["db"] and (fix_x or fix_d) and is_placeholder_fb(df_) \
                    and str(df_ or "").strip() != ABSENT_NOTE
                if not (fix_x or fix_d):
                    continue
                where = [n for n, f in (("workbook", fix_x), ("database", fix_d)) if f]
                # the database is the source of truth: keep its note (e.g. "signature missing") when it has one
                fb_value = str(df_).strip() if (ds == 0 and not is_placeholder_fb(df_)) else ABSENT_NOTE
                if fb_x is False and it["fb_column"] and fb_value != ABSENT_NOTE and is_placeholder_fb(xf):
                    fb_x = True
                out.append({
                    "Course": code, "Section": str(sec), "Item": it["column"], "Week": week,
                    "Student ID": sid, "Full Name": str(r.get("Full Name") or ""),
                    "Workbook": "empty" if xs is None else "0",
                    "Database": ("—" if not it["db"] else ("no record" if row_idx is None
                                                          else ("empty" if ds is None else "0"))),
                    "Change": "0 in " + " and ".join(where),
                    "_wb": wb, "_col": it["column"], "_fbcol": it["fb_column"] if fb_x else None,
                    "_fb_value": fb_value,
                    "_fix_x": fix_x, "_fix_d": fix_d, "_fb_d": fb_d, "_db_row": row_idx,
                    "_qn": it["number"], "_criterion": it["criterion"], "_db": it["db"],
                })
    return out


def plan(cfg: dict, db: pd.DataFrame | None = None) -> list[dict]:
    d = prepare_db(dbio.read_records() if db is None else db)
    out = []
    for code in cfgmod.all_courses(cfg):
        out += plan_course(cfg["courses"][code], d)
    return out


def summarize(changes: list[dict]) -> list[dict]:
    """Counts per course / section / item, for the report and the UI."""
    c = Counter((x["Course"], x["Section"], x["Item"], x["Week"]) for x in changes)
    return [{"Course": k[0], "Section": k[1], "Item": k[2], "Week": f"Week {k[3]}", "Zeros": n}
            for k, n in sorted(c.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][3], kv[0][2]))]


def public(changes: list[dict]) -> list[dict]:
    """Changes without the internal fields (for tables)."""
    return [{k: v for k, v in x.items() if not k.startswith("_")} for x in changes]


# ----------------------------------------------------------------------------- apply
def files_of(changes: list[dict]) -> list[str]:
    files = sorted({x["_wb"] for x in changes if x["_fix_x"] or x["_fbcol"]})
    if any(x["_fix_d"] or x["_fb_d"] for x in changes):
        files.append(DB_FILE)
    return files


def apply(cfg: dict, changes: list[dict] | None = None) -> dict:
    """Write the planned zeros. Every file is checked for an open Excel window first and backed up
    (stamped) before it is changed. Returns {"zeros", "summary", "files", "publish", "log"}."""
    changes = plan(cfg) if changes is None else changes
    result = {"zeros": len(changes), "summary": summarize(changes), "files": [], "publish": [], "log": []}
    if not changes:
        result["log"].append("No absent records without a score were found. Nothing was changed.")
        return result
    files = files_of(changes)
    ensure_writable(*files)                                 # nothing is written if any file is open
    from . import badges
    # course workbooks (only values change; formatting stays exactly as in the template)
    by_wb: dict = defaultdict(lambda: defaultdict(dict))
    course_of: dict = {}
    for x in changes:
        course_of[x["_wb"]] = x["Course"]
        if x["_fix_x"]:
            by_wb[x["_wb"]][x["_col"]][x["Student ID"]] = 0
        if x["_fbcol"]:
            by_wb[x["_wb"]][x["_fbcol"]][x["Student ID"]] = x.get("_fb_value") or ABSENT_NOTE
    for wb, cols in by_wb.items():
        code = course_of[wb]
        term = cfgmod.course(cfg, code).get("term")
        soft = {c: list(v) for c, v in cols.items()}        # only empty / placeholder cells are filled
        r = xio.write_many(wb, cols, soft_keys=soft, create_column=False, term=term, course=code)
        result["files"].append(wb)
        result["log"].append(f"{wb}: {r['cells']} cell(s) updated on sheet(s) {', '.join(r['sheets']) or '—'}.")
        exp_cols = [x["_col"] for x in changes if x["_wb"] == wb and x["_criterion"] == "Experiment"]
        if exp_cols:
            badges.mark_updates(code, student_columns=[(x["_col"], x["Student ID"]) for x in changes
                                                       if x["_wb"] == wb and x["_criterion"] == "Experiment"])
            result["publish"] += [wb, badges.UPDATES_FILE]
    # portal database
    db_changes = [x for x in changes if x["_db"] and (x["_fix_d"] or x["_fb_d"])]
    if db_changes:
        db = dbio.read_records()
        for c in dbio.RECORD_COLUMNS:
            if c not in db.columns:
                db[c] = ""
        for c in ("quiz_score", "feedback", "quiz_no", "updated_at", "attendance", "attended_section"):
            db[c] = db[c].astype(object)
        stamp_now, new_rows = now_iso(), []
        for x in db_changes:
            label = f"Quiz {x['_qn']}"
            i = x["_db_row"]
            if i is not None and i in db.index:
                db.at[i, "quiz_no"] = label
                if x["_fix_d"]:
                    db.at[i, "quiz_score"] = 0
                if x["_fb_d"]:
                    db.at[i, "feedback"] = ABSENT_NOTE
                db.at[i, "updated_at"] = stamp_now
            else:
                new_rows.append({"student_id": x["Student ID"], "full_name": x["Full Name"] or "Student",
                                 "course_code": x["Course"], "section": x["Section"], "week": f"Week {x['Week']}",
                                 "attendance": "-", "quiz_no": label, "quiz_score": 0, "feedback": ABSENT_NOTE,
                                 "attended_section": "", "updated_at": stamp_now})
        if new_rows:
            db = pd.concat([db, pd.DataFrame(new_rows)], ignore_index=True)
        terms = {cfgmod.course(cfg, x["Course"]).get("term") for x in db_changes}
        dbio.write(records=db, term=next(iter(terms)) if len(terms) == 1 else None)
        touch_meta()
        result["files"].append(DB_FILE)
        result["log"].append(f"{DB_FILE}: {len(db_changes) - len(new_rows)} record(s) updated, "
                             f"{len(new_rows)} added.")
    result["publish"] = sorted(set(result["publish"]) | ({DB_FILE, "portal_meta.json"} if db_changes else set()))
    return result
