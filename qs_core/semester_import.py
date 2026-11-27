"""Start-of-semester import from the university grade-report exports.

Reads every "Öğretim Elemanı Not Excel Raporu*.xlsx" file (also "... (1).xlsx", "... (2).xlsx", uploads),
groups the rows by course code (Ders Kodu) and, for each selected course:

  * creates the course in courses_config.json when it is new (sections taken from the file);
  * creates or updates <COURSE>_attendance.xlsx and <COURSE>_grades.xlsx (and any other configured
    course workbook) with one worksheet per section, named exactly after the section, cloned from the
    template formatting; existing grades/attendance are kept (mid-semester re-sync);
  * writes the course roster into the "Roster" sheet of grades_database.xlsx so the student portal
    recognises every student before the first week is graded.
"""
from __future__ import annotations

import glob
import os
import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from . import config as cfgmod
from . import dbio
from . import excel_io as xio
from . import roster as rmod
from .common import BASE_DIR, INPUT_DIR, clean_id, now_iso, path

REPORT_STEM = "ogretim elemani not excel raporu"          # normalised form of the file name
REPORT_HINT = "university grade report export (Öğretim Elemanı Not Excel Raporu*.xlsx)"
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _norm_name(name: str) -> str:
    s = unicodedata.normalize("NFC", os.path.basename(name))
    return rmod._norm(os.path.splitext(s)[0])


def is_report_file(name: str) -> bool:
    """True for 'Öğretim Elemanı Not Excel Raporu.xlsx', '... (1).xlsx', '...(2).xlsx', etc."""
    if not name.lower().endswith(".xlsx") or os.path.basename(name).startswith("~$"):
        return False
    n = _norm_name(name)
    return n == REPORT_STEM or re.fullmatch(re.escape(REPORT_STEM) + r"\s*\d+", n) is not None


def find_report_files(folders=None) -> list[str]:
    """Report files in the workspace root and input/rosters/ (absolute paths, sorted)."""
    folders = folders or [BASE_DIR, path(INPUT_DIR, "rosters")]
    out = []
    for d in folders:
        if os.path.isdir(d):
            for p in glob.glob(os.path.join(d, "*.xlsx")):
                if is_report_file(p) and p not in out:
                    out.append(p)
    return sorted(out, key=lambda p: (len(os.path.basename(p)), os.path.basename(p)))


def safe_sheet_name(section: str) -> str:
    s = _BAD_SHEET_CHARS.sub("-", str(section)).strip() or "Section"
    return s[:31]


@dataclass
class CourseGroup:
    code: str
    title: str
    students: pd.DataFrame                     # Student ID, Full Name, Section, Department
    files: list = field(default_factory=list)

    @property
    def sections(self) -> list[str]:
        return sorted(set(self.students["Section"]), key=xio.section_sort_key)


@dataclass
class ImportResult:
    courses: dict = field(default_factory=dict)       # code -> CourseGroup
    files: list = field(default_factory=list)         # [(name, rows, courses)]
    warnings: list = field(default_factory=list)


def read_reports(sources: list[tuple[str, bytes]], section_column: str = "Section") -> ImportResult:
    """sources = [(file_name, bytes)]. Rows are grouped by course code; later files win on duplicates."""
    res = ImportResult()
    frames = []
    for name, data in sources:
        try:
            raw = pd.read_excel(__import__("io").BytesIO(data), sheet_name=None, header=None, dtype=str)
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"{name}: could not be read ({e}).")
            continue
        found = False
        for sheet, df in raw.items():
            df = df.dropna(how="all").reset_index(drop=True)
            if df.empty:
                continue
            mapping, fmt = rmod._map_header_row(df.iloc[0].tolist())
            if fmt != rmod.GRADE_REPORT_FORMAT:
                continue
            found = True
            body = df.iloc[1:]

            def col(canon):
                return body.iloc[:, mapping[canon][0]] if canon in mapping else pd.Series([""] * len(body))
            sec_canon = section_column if section_column in mapping else "Section"
            first = col("First Name").fillna("").astype(str).str.strip()
            last = col("Last Name").fillna("").astype(str).str.strip()
            part = pd.DataFrame({
                "Course Code": col("Course Code").map(rmod._course_key).values,
                "Course Title": col("Course Name").fillna("").astype(str).str.strip().values,
                "Student ID": col("Student ID").map(clean_id).values,
                "Full Name": (first + " " + last).str.replace(r"\s+", " ", regex=True).str.strip().values,
                "Section": col(sec_canon).map(rmod.normalise_section_value).values,
                "Department": col("Department").fillna("").astype(str).str.strip().values,
                "_file": name,
            })
            part = part[(part["Student ID"].str.len() >= 5) & (part["Course Code"] != "")]
            frames.append(part)
            res.files.append((name, len(part), sorted(set(part["Course Code"]))))
        if not found:
            res.warnings.append(f"{name}: this is not the expected {REPORT_HINT} layout; it was skipped. "
                                f"Use Roster Sync for other roster formats.")
    if not frames:
        return res
    allrows = pd.concat(frames, ignore_index=True)
    dup = allrows[allrows.duplicated(["Course Code", "Student ID"], keep=False)]
    if not dup.empty:
        conflicts = dup.groupby(["Course Code", "Student ID"])["Section"].nunique()
        n_conf = int((conflicts > 1).sum())
        res.warnings.append(f"{dup.groupby(['Course Code', 'Student ID']).ngroups} student(s) appear in more than "
                            f"one file; the last file wins"
                            + (f" ({n_conf} with different sections)." if n_conf else "."))
    allrows = allrows.drop_duplicates(["Course Code", "Student ID"], keep="last")
    blank = allrows[allrows["Section"] == ""]
    if not blank.empty:
        res.warnings.append(f"{len(blank)} student(s) have an empty '{section_column}' value and were placed in "
                            f"section 'Unassigned'.")
        allrows.loc[allrows["Section"] == "", "Section"] = "Unassigned"
    renamed = {s: safe_sheet_name(s) for s in set(allrows["Section"]) if safe_sheet_name(s) != s}
    if renamed:
        res.warnings.append("Some section names are not valid Excel sheet names and were adjusted: "
                            + ", ".join(f"{a} -> {b}" for a, b in renamed.items()))
        allrows["Section"] = allrows["Section"].map(lambda s: renamed.get(s, s))
    for code, g in allrows.groupby("Course Code"):
        title = next((t for t in g["Course Title"] if t), "")
        res.courses[code] = CourseGroup(code, title, g[["Student ID", "Full Name", "Section", "Department"]]
                                        .sort_values(["Section", "Student ID"]).reset_index(drop=True),
                                        sorted(set(g["_file"])))
    return res


def read_report_files(paths: list[str], section_column: str = "Section") -> ImportResult:
    srcs = []
    for p in paths:
        with open(p, "rb") as f:
            srcs.append((os.path.basename(p), f.read()))
    return read_reports(srcs, section_column)


def new_course_config(group: CourseGroup, term: str, weeks: int, quiz_count: int, section_column: str) -> dict:
    c = {"code": group.code, "title": group.title, "term": term, "sections": group.sections, "weeks": int(weeks),
         "show_standing": False, "roster_section_column": section_column,
         "components": [{"label": "Quiz", "type": "quiz", "weight": 0.0, "count": int(quiz_count), "rule": "all"}]}
    return cfgmod.normalise_course(c, group.code)


def preview(cfg: dict, result: ImportResult) -> list[dict]:
    rows = []
    for code, g in sorted(result.courses.items()):
        c = cfgmod.course(cfg, code)
        if c is None:
            status, changes = "New course", f"{len(g.students)} students to create"
        else:
            d = rmod.diff_roster(c, g.students)
            status = "Workbooks missing" if d.missing_files else "Existing course"
            changes = (f"{len(d.added)} new, {len(d.dropped)} dropped, {len(d.section_changes)} section change(s), "
                       f"{len(d.name_changes)} name correction(s)")
        rows.append({"Course": code, "Title": g.title, "Students": len(g.students),
                     "Sections (sheets)": ", ".join(f"{s} ({int((g.students['Section'] == s).sum())})"
                                                    for s in g.sections),
                     "Status": status, "Changes": changes, "Source file(s)": ", ".join(g.files)})
    return rows


def apply(cfg: dict, result: ImportResult, codes: list[str], term: str, section_column: str = "Section",
          weeks: int = 14, quiz_count: int = 9, remove_dropped: bool = False) -> list[str]:
    """Create/update the selected courses. Returns an English change log."""
    log = []
    ros_all = dbio.read_roster()
    for code in codes:
        g = result.courses[code]
        c = cfgmod.course(cfg, code)
        if c is None:
            c = new_course_config(g, term, weeks, quiz_count, section_column)
            cfg["courses"][code] = c
            log.append(f"{code}: added to the course configuration (term {term}, sections "
                       f"{', '.join(c['sections'])}, {c['weeks']} weeks, {quiz_count} quizzes). Set the "
                       f"component weights in Course Setup to show standings to students.")
        else:
            new_secs = [s for s in g.sections if s not in c["sections"]]
            if new_secs:
                c["sections"] = sorted(set(c["sections"]) | set(new_secs), key=xio.section_sort_key)
                log.append(f"{code}: new section(s) {', '.join(new_secs)} added to the configuration.")
            c["roster_section_column"] = section_column
        if term and term not in cfg["terms"]:
            cfg["terms"].append(term)
        cfgmod.save(cfg)
        for line in rmod.apply_roster(c, g.students, remove_dropped=remove_dropped):
            log.append(f"{code}: {line}")
        ts = now_iso()
        rows = [{"student_id": r["Student ID"], "full_name": r["Full Name"], "course_code": code,
                 "course_title": g.title or c.get("title", ""), "section": r["Section"],
                 "department": r["Department"], "term": c.get("term") or term, "updated_at": ts}
                for r in g.students.to_dict("records")]
        ros_all = dbio.replace_course_roster(ros_all, code, rows)
    dbio.write(roster=ros_all, term=term)
    log.append(f"grades_database.xlsx: 'Roster' sheet updated for {', '.join(codes)} "
               f"({sum(len(result.courses[c].students) for c in codes)} students).")
    return log
