"""Class roster import and synchronisation.

parse_roster()  reads a school-exported roster (.xlsx / .xls / .csv), finds the header row and maps
                its columns (English or Turkish headers) to Student ID / Full Name / Section / Department.
diff_roster()   compares a roster with a course's existing workbooks: new students, dropped/missing
                students, section changes (e.g. 901 -> 902) and name changes.
apply_roster()  initial setup (generates every course-coded workbook) or mid-semester update
                (adds rows, moves changed sections with their records, optionally removes dropped
                students) while keeping grades, feedback, attendance and template formatting.
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
from dataclasses import dataclass, field

import openpyxl
import pandas as pd

from . import backup as bk
from . import config as cfgmod
from . import dbio
from . import excel_io as xio
from .common import DB_FILE, clean_id, clean_section, ensure_writable, is_blank, path

# Header aliases (normalised: lower case, accents removed, non-letters collapsed)
_ALIASES = {
    "Student ID": ["student id", "student no", "student number", "studentid", "id", "ogrenci no",
                   "ogrenci numarasi", "ogr no", "numara", "no", "school number", "okul no"],
    "Full Name": ["full name", "name surname", "student name", "name", "ad soyad", "adi soyadi",
                  "adsoyad", "ogrenci adi soyadi", "isim soyisim", "ad soyadi"],
    "First Name": ["first name", "given name", "ad", "adi", "isim"],
    "Last Name": ["last name", "surname", "family name", "soyad", "soyadi", "soyisim"],
    "Section": ["section", "sec", "group", "sube", "grup", "section no", "sube no", "class section"],
    "Lab Section": ["lab section", "lab sube", "lab subesi", "laboratuvar subesi", "lab group", "lab"],
    "Department": ["department", "program", "programme", "bolum", "bolumu", "bolum adi", "program adi",
                   "fakulte bolum", "major"],
    "Course Code": ["course code", "ders kodu", "course", "ders"],
    "Course Name": ["course name", "course title", "ders adi"],
}
# Canonical fields that may only be matched exactly (their loose aliases are too generic)
_EXACT_ONLY = {"Course Code", "Course Name", "Lab Section"}

# University "instructor grade report" export (sheet "Worksheet"). Columns, in order:
# Student Program ID, Öğrenci Numarası, Adı, Soyadı, Program, Ders Kodu, Ders Adı, Section,
# Harf Notu, Bütünleme Harf Notu, Lab Section, Ders Kayıt Tarihi
_GRADE_REPORT = {
    "student program id": None,            # internal program record ID, NOT the student number
    "ogrenci numarasi": "Student ID",
    "adi": "First Name",
    "soyadi": "Last Name",
    "program": "Department",
    "ders kodu": "Course Code",
    "ders adi": "Course Name",
    "section": "Section",
    "harf notu": None,
    "butunleme harf notu": None,
    "lab section": "Lab Section",
    "ders kayit tarihi": None,
}
GRADE_REPORT_FORMAT = "University instructor grade report export"
SECTION_COLUMNS = ["Section", "Lab Section"]


def _norm(s) -> str:
    s = str(s or "").strip()
    s = s.replace("İ", "I").replace("ı", "i")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def _map_header_row(values) -> tuple[dict, str]:
    """{canonical: (column_index, original_header)} and the detected format name."""
    norm = [_norm(v) for v in values]
    present = {n for n in norm if n}
    required = {"ogrenci numarasi", "adi", "soyadi", "ders kodu", "section"}
    if required <= present and len(present & set(_GRADE_REPORT)) >= 8:
        mapping = {}
        for c, n in enumerate(norm):
            canon = _GRADE_REPORT.get(n)
            if canon and canon not in mapping:
                mapping[canon] = (c, str(values[c]).strip())
        return mapping, GRADE_REPORT_FORMAT

    mapping, used = {}, set()
    for c, n in enumerate(norm):                       # 1) exact alias matches
        for canon, al in _ALIASES.items():
            if n in al and canon not in mapping:
                mapping[canon] = (c, str(values[c]).strip())
                used.add(c)
                break
    for c, n in enumerate(norm):                       # 2) loose matches for what is still missing
        if c in used or not n:
            continue
        tokens = n.split()
        for canon, al in _ALIASES.items():
            if canon in mapping or canon in _EXACT_ONLY:
                continue
            if "id" in tokens and canon != "Student ID":
                continue                               # e.g. "Student Program ID" is never a department
            if canon == "Student ID" and "id" in tokens and len(tokens) > 2:
                continue                               # "Student Program ID" is not the student number
            if canon == "Section" and "lab" in tokens:
                continue
            if any(len(a) > 3 and a in n for a in al):
                mapping[canon] = (c, str(values[c]).strip())
                used.add(c)
                break
    return mapping, "Generic roster"


def _match(header) -> str | None:
    """Canonical field of a single header (kept for compatibility)."""
    m, _ = _map_header_row([header])
    return next(iter(m), None)


def normalise_section_value(val) -> str:
    """'901' -> '901', 4 -> '4', '4.0' -> '4', 'Section 902' -> '902'."""
    s = clean_section(val)
    if not s:
        return ""
    if not re.fullmatch(r"\d+", s):
        m = re.search(r"\d+", s)
        s = m.group() if m else s
    return s


def _course_key(val) -> str:
    return re.sub(r"\s+", "", str(val or "")).upper()


@dataclass
class RosterResult:
    students: pd.DataFrame
    mapping: dict = field(default_factory=dict)        # canonical -> original header
    header_row: int = 0
    sheet: str = ""
    warnings: list = field(default_factory=list)
    format: str = "Generic roster"
    section_columns: list = field(default_factory=list)   # grouping columns available in the file
    section_column: str = "Section"                       # grouping column actually used
    course_codes: dict = field(default_factory=dict)      # course code -> student rows in the file
    excluded_other_courses: int = 0


def _frames_from_file(data: bytes, filename: str) -> list[tuple[str, pd.DataFrame]]:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".csv":
        for enc in ("utf-8-sig", "cp1254", "latin-1"):
            try:
                txt = data.decode(enc)
                sep = ";" if txt.count(";") > txt.count(",") else ","
                return [("csv", pd.read_csv(io.StringIO(txt), header=None, dtype=str, sep=sep))]
            except Exception:
                continue
        raise ValueError("The CSV file could not be decoded.")
    engine = "xlrd" if ext == ".xls" else None
    try:
        book = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str, engine=engine)
    except ImportError as e:
        raise ValueError("Reading .xls files needs the 'xlrd' package. Save the roster as .xlsx and "
                         "upload it again.") from e
    return list(book.items())


def parse_roster(data: bytes, filename: str, default_section: str | None = None,
                 section_column: str = "Section", course_code: str | None = None) -> RosterResult:
    """Read a roster file.

    section_column  "Section" or "Lab Section": which column groups students into section sheets.
    course_code     when the file has a course-code column (e.g. "Ders Kodu"), keep only that course's rows.
    """
    best = None
    for sheet, raw in _frames_from_file(data, filename):
        raw = raw.dropna(how="all").reset_index(drop=True)
        for hr in range(min(25, len(raw))):
            mapping, fmt = _map_header_row(raw.iloc[hr].tolist())
            if "Student ID" in mapping and ("Full Name" in mapping or "First Name" in mapping):
                score = len(mapping) + (10 if fmt == GRADE_REPORT_FORMAT else 0)
                if not best or score > best[0]:
                    best = (score, sheet, raw, hr, mapping, fmt)
                break
    if not best:
        raise ValueError("Could not find the header row. The roster needs at least a student ID column and a "
                         "name column (English headers such as 'Student ID' / 'Full Name' and the usual Turkish "
                         "school-export headers are both recognised).")
    _, sheet, raw, hr, mapping, fmt = best
    body = raw.iloc[hr + 1:]
    warnings = []
    available = [c for c in SECTION_COLUMNS if c in mapping]
    used_col = section_column if section_column in mapping else (available[0] if available else "")
    if section_column not in mapping and section_column in SECTION_COLUMNS and available:
        warnings.append(f"The file has no '{section_column}' column; '{used_col}' is used instead.")

    def cell(row, canon):
        return row.iloc[mapping[canon][0]] if canon in mapping else None

    codes, recs, excluded = {}, [], 0
    want = _course_key(course_code) if course_code else ""
    sheet_sec = clean_section(sheet) if re.fullmatch(r"\d{1,4}", str(sheet).strip()) else ""
    for _, row in body.iterrows():
        sid = clean_id(cell(row, "Student ID"))
        if len(sid) < 5:
            continue
        code = _course_key(cell(row, "Course Code")) if "Course Code" in mapping else ""
        if code:
            codes[code] = codes.get(code, 0) + 1
        if want and code and code != want:
            excluded += 1
            continue
        if "Full Name" in mapping:
            name = cell(row, "Full Name")
        else:
            first, last = cell(row, "First Name"), cell(row, "Last Name")
            name = f"{'' if is_blank(first) else first} {'' if is_blank(last) else last}"
        name = re.sub(r"\s+", " ", "" if is_blank(name) else str(name)).strip()
        sec = normalise_section_value(cell(row, used_col)) if used_col else ""
        sec = sec or sheet_sec or (default_section or "")
        dept = cell(row, "Department")
        recs.append({"Student ID": sid, "Full Name": name, "Section": sec,
                     "Department": "" if is_blank(dept) else str(dept).strip()})
    df = pd.DataFrame(recs, columns=["Student ID", "Full Name", "Section", "Department"])
    if want and codes and want not in codes:
        warnings.append(f"This file contains course(s) {', '.join(sorted(codes))}, not {course_code}. "
                        f"Untick 'Only import rows for this course' to import it anyway.")
    elif excluded:
        warnings.append(f"{excluded} row(s) for other courses ({', '.join(c for c in sorted(codes) if c != want)}) "
                        f"were skipped.")
    dup = df[df.duplicated("Student ID", keep=False)]
    if not dup.empty:
        warnings.append(f"{dup['Student ID'].nunique()} student ID(s) appear more than once; the first row is used.")
        df = df.drop_duplicates("Student ID", keep="first")
    if not df.empty and (df["Section"] == "").any():
        warnings.append(f"{int((df['Section'] == '').sum())} student(s) have no section. Choose a default "
                        f"section for this file.")
    return RosterResult(df.reset_index(drop=True), {k: v[1] for k, v in mapping.items()}, hr + 1, str(sheet),
                        warnings, fmt, available, used_col or "Section", codes, excluded)


def combine(results: list[RosterResult]) -> pd.DataFrame:
    frames = [r.students for r in results if not r.students.empty]
    if not frames:
        return pd.DataFrame(columns=["Student ID", "Full Name", "Section", "Department"])
    return pd.concat(frames, ignore_index=True).drop_duplicates("Student ID", keep="last").reset_index(drop=True)


# ----------------------------------------------------------------------------- diff
@dataclass
class RosterDiff:
    added: list = field(default_factory=list)            # roster rows not in workbooks
    dropped: list = field(default_factory=list)          # workbook students missing from roster
    section_changes: list = field(default_factory=list)  # {"Student ID","Full Name","from","to"}
    name_changes: list = field(default_factory=list)
    unchanged: int = 0
    initial: bool = False
    missing_files: list = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.dropped or self.section_changes or self.name_changes or self.missing_files)


def existing_students(course: dict) -> dict:
    """{sid: {...}} from the attendance workbook (fallback: grades, then component workbooks)."""
    for f in cfgmod.course_files(course):
        rows = xio.read_students(f)
        if rows:
            return {r["Student ID"]: r for r in rows}
    return {}


def diff_roster(course: dict, roster: pd.DataFrame) -> RosterDiff:
    d = RosterDiff()
    d.missing_files = [f for f in cfgmod.course_files(course) if not os.path.exists(path(f))]
    cur = existing_students(course)
    d.initial = not cur
    new = {r["Student ID"]: r for r in roster.to_dict("records")}
    for sid, r in new.items():
        if sid not in cur:
            d.added.append(r)
            continue
        old = cur[sid]
        if r["Section"] and old["Section"] and r["Section"] != old["Section"]:
            d.section_changes.append({"Student ID": sid, "Full Name": r["Full Name"] or old["Full Name"],
                                      "from": old["Section"], "to": r["Section"]})
        elif r["Full Name"] and old["Full Name"] and _norm(r["Full Name"]) != _norm(old["Full Name"]):
            d.name_changes.append({"Student ID": sid, "old": old["Full Name"], "new": r["Full Name"]})
        else:
            d.unchanged += 1
    for sid, old in cur.items():
        if sid not in new:
            d.dropped.append({"Student ID": sid, "Full Name": old["Full Name"], "Section": old["Section"]})
    return d


# ----------------------------------------------------------------------------- apply
def _kind_of(course: dict, filename: str) -> tuple[str, dict | None]:
    if filename == course["workbooks"]["attendance"]:
        return "attendance", None
    if filename == course["workbooks"]["grades"]:
        return "grades", None
    comp = next((c for c in cfgmod.workbook_components(course) if c["workbook"] == filename), None)
    return "component", comp


def _ensure_section_sheet(wb, course: dict, kind: str, comp: dict | None, section: str):
    if section in wb.sheetnames:
        return wb[section]
    tkey = "attendance" if kind == "attendance" else ("grades" if kind == "grades" else "component")
    sec_prof, _, _ = xio._profiles(tkey)
    # Clone the look of an existing section sheet of this workbook when there is one
    donor = next((w for w in wb.worksheets if xio.is_section_sheet(w.title)), None)
    if donor is not None:
        ws = wb.copy_worksheet(donor)
        ws.title = section
        for rng in list(ws.merged_cells.ranges):
            if rng.min_row >= 2:
                ws.unmerge_cells(str(rng))
        if ws.max_row > 2:
            ws.delete_rows(3, ws.max_row - 2)
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(2, c)
            if not isinstance(cell, openpyxl.cell.cell.MergedCell):
                cell.value = None
    else:
        ws = wb.create_sheet(section)
        xio._build_sheet(ws, sec_prof, sec_prof.fixed_headers, xio.dynamic_headers(kind, course, comp), [],
                         lambda s: section, kind, course["weeks"])
    # keep section sheets before "All Sections", in numeric order
    order = sorted([w for w in wb.worksheets if xio.is_section_sheet(w.title)],
                   key=lambda w: xio.section_sort_key(w.title))
    order += [w for w in wb.worksheets if not xio.is_section_sheet(w.title)]
    wb._sheets = order
    return ws


def _row_values(ws, r: int) -> dict:
    hm = xio.header_map(ws)
    return {h: ws.cell(r, c).value for h, c in hm.items()}


def apply_roster(course: dict, roster: pd.DataFrame, remove_dropped: bool = False,
                 update_names: bool = True, update_database: bool = True) -> list[str]:
    """Apply the roster to every course workbook. Returns an English change log."""
    log = []
    term, code = course.get("term"), course["code"]
    ensure_writable(*cfgmod.course_files(course), *([DB_FILE] if update_database else []))
    diff = diff_roster(course, roster)
    roster_map = {r["Student ID"]: r for r in roster.to_dict("records")}

    # 1) Missing workbooks are generated from the templates with the full roster
    for f in diff.missing_files:
        kind, comp = _kind_of(course, f)
        xio.build_workbook(f, kind, course, list(roster_map.values()), comp=comp)
        log.append(f"Created {f} from the template ({len(roster_map)} students, "
                   f"{len(set(roster['Section']))} section sheet(s)).")

    moves = {m["Student ID"]: m for m in diff.section_changes}
    added = {r["Student ID"]: r for r in diff.added}
    dropped = {r["Student ID"] for r in diff.dropped}
    renames = {r["Student ID"]: r["new"] for r in diff.name_changes} if update_names else {}

    for f in cfgmod.course_files(course):
        if f in diff.missing_files:
            continue
        p = path(f)
        kind, comp = _kind_of(course, f)
        bk.backup_file(p, term=term, course=code)
        wb = openpyxl.load_workbook(p)
        stats = {"added": 0, "moved": 0, "removed": 0, "renamed": 0}
        all_ws = wb[xio.ALL_SECTIONS] if xio.ALL_SECTIONS in wb.sheetnames else None

        # Moves: copy the whole record (grades/attendance/feedback) to the new section sheet
        for sid, mv in moves.items():
            src_ws = wb[mv["from"]] if mv["from"] in wb.sheetnames else None
            if src_ws is None:
                continue
            hm = xio.header_map(src_ws)
            r = xio.id_rows(src_ws, hm.get(xio.ID_HEADER, 1)).get(sid)
            if not r:
                continue
            vals = _row_values(src_ws, r)
            dst_ws = _ensure_section_sheet(wb, course, kind, comp, mv["to"])
            vals = {h: v for h, v in vals.items()
                    if not (isinstance(v, str) and v.startswith("="))}      # formulas are re-created
            xio.append_student(dst_ws, vals)
            xio.delete_row(src_ws, r)
            stats["moved"] += 1

        # New students
        for sid, st in added.items():
            sec = st["Section"] or (course["sections"][0] if course["sections"] else "")
            ws = _ensure_section_sheet(wb, course, kind, comp, sec)
            xio.append_student(ws, {xio.ID_HEADER: sid, xio.NAME_HEADER: st["Full Name"],
                                    xio.SECTION_HEADER: sec, xio.DEPT_HEADER: st.get("Department", "")})
            stats["added"] += 1

        # Dropped students (only when asked; their data stays in the backup)
        if remove_dropped:
            for ws in wb.worksheets:
                hm = xio.header_map(ws)
                if xio.ID_HEADER not in hm:
                    continue
                for sid, r in sorted(xio.id_rows(ws, hm[xio.ID_HEADER]).items(), key=lambda x: -x[1]):
                    if sid in dropped:
                        xio.delete_row(ws, r)
                        if xio.is_section_sheet(ws.title):
                            stats["removed"] += 1

        # All Sections: add new / moved students, refresh Section and Full Name columns
        if all_ws is not None:
            hm = xio.header_map(all_ws)
            rows = xio.id_rows(all_ws, hm.get(xio.ID_HEADER, 1))
            for sid in list(added):
                if sid not in rows:
                    st = added[sid]
                    xio.append_student(all_ws, {xio.ID_HEADER: sid, xio.NAME_HEADER: st["Full Name"],
                                                xio.SECTION_HEADER: st["Section"],
                                                xio.DEPT_HEADER: st.get("Department", "")})
            rows = xio.id_rows(all_ws, hm.get(xio.ID_HEADER, 1))
            for sid, mv in moves.items():
                if sid in rows and xio.SECTION_HEADER in hm:
                    all_ws.cell(rows[sid], hm[xio.SECTION_HEADER]).value = mv["to"]
            xio.relink_all_sections(wb)
            if kind == "component":
                xio.link_sections_to_all(wb)  # new or moved students: their section cells show All Sections

        # Name corrections
        if renames:
            for ws in wb.worksheets:
                hm = xio.header_map(ws)
                if xio.ID_HEADER not in hm or xio.NAME_HEADER not in hm:
                    continue
                for sid, r in xio.id_rows(ws, hm[xio.ID_HEADER]).items():
                    if sid in renames:
                        ws.cell(r, hm[xio.NAME_HEADER]).value = renames[sid]
                        if xio.is_section_sheet(ws.title):
                            stats["renamed"] += 1

        wb.save(p)
        log.append(f"Updated {f}: {stats['added']} added, {stats['moved']} moved to a new section, "
                   f"{stats['removed']} removed, {stats['renamed']} name(s) corrected.")

    if update_database and (moves or renames) and os.path.exists(path(DB_FILE)):
        db = dbio.read_records()
        db["student_id"] = db["student_id"].map(clean_id)
        db["section"] = db["section"].map(clean_section).astype(object)
        db["full_name"] = db["full_name"].astype(object)
        m_course = db["course_code"].astype(str).str.strip() == code
        n = 0
        for sid, mv in moves.items():
            mask = m_course & (db["student_id"] == sid)
            n += int(mask.sum())
            db.loc[mask, "section"] = mv["to"]
        for sid, nm in renames.items():
            db.loc[m_course & (db["student_id"] == sid), "full_name"] = nm
        dbio.write(records=db, term=term)
        log.append(f"Updated {DB_FILE}: {n} portal record(s) moved to the new section"
                   + (f", {len(renames)} name(s) corrected." if renames else "."))

    if diff.dropped and not remove_dropped:
        log.append(f"{len(diff.dropped)} student(s) are no longer on the roster. They were kept in the "
                   f"workbooks with all their records (enable 'Remove dropped students' to delete them).")
    return log
