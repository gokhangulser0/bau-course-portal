"""Template-preserving Excel engine.

Every workbook written by the system keeps the exact look of the base/template workbooks:
row heights, column widths, borders, fonts, fills, number formats and alignments are cloned
from the template (for new workbooks/sheets/columns) or from the neighbouring row of the same
sheet (for new students). Only cell values change when scores, attendance or feedback are written.

Conventions (taken from the existing MCH2008 workbooks):
  * one worksheet per section ("901", "902", ...) plus an "All Sections" worksheet;
  * header in row 1, one student per row from row 2;
  * "Student ID" stored as text (number format "@"), so IDs never become floats.
"""
from __future__ import annotations

import os
import re
import shutil
from copy import copy
from dataclasses import dataclass, field

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter

from . import backup as bk
from . import config as cfgmod
from .common import TEMPLATE_DIR, clean_id, clean_section, ensure_writable, is_blank, path

ALL_SECTIONS = "All Sections"
ID_HEADER = "Student ID"
NAME_HEADER = "Full Name"
SECTION_HEADER = "Section"
DEPT_HEADER = "Department"

TEMPLATES = {
    "attendance": "attendance_template.xlsx",
    "grades": "grades_template.xlsx",
    "component": "component_template.xlsx",
}
# Where the templates were cloned from (the original MCH2008 base workbooks)
TEMPLATE_SOURCES = {
    "attendance": "MCH2008_attendance.xlsx",
    "grades": "MCH2008_grades.xlsx",
    "component": "MCH2008_homework.xlsx",
}

_FORMULA_SHEET_REF = re.compile(r"(?P<sheet>'[^']+'|[A-Za-z0-9_]+)!(?P<col>\$?[A-Z]{1,3})(?P<row>\$?\d+)")


# ----------------------------------------------------------------------------- column kinds
def classify(header) -> str:
    h = str(header or "").strip()
    hl = h.lower()
    if hl in ("student id", "student no", "id"):
        return "id"
    if hl in ("full name", "name", "student name"):
        return "name"
    if hl == "section":
        return "section"
    if hl == "department":
        return "department"
    if re.fullmatch(r"week\s*#?\s*\d+", hl):
        return "mark"
    if hl.startswith("total attendance"):
        return "total"
    if hl.endswith(" fb") or hl.endswith("feedback"):
        return "feedback"
    return "score"


FIXED_KINDS = ("id", "name", "section", "department")


# ----------------------------------------------------------------------------- style helpers
@dataclass
class CellStyle:
    font: object = None
    border: object = None
    fill: object = None
    alignment: object = None
    number_format: str = "General"
    protection: object = None

    @classmethod
    def of(cls, cell) -> "CellStyle":
        return cls(copy(cell.font), copy(cell.border), copy(cell.fill), copy(cell.alignment),
                   cell.number_format, copy(cell.protection))

    def apply(self, cell) -> None:
        if isinstance(cell, MergedCell):
            return
        cell.font = copy(self.font)
        cell.border = copy(self.border)
        cell.fill = copy(self.fill)
        cell.alignment = copy(self.alignment)
        cell.number_format = self.number_format
        cell.protection = copy(self.protection)


def copy_cell_style(src, dst) -> None:
    if isinstance(dst, MergedCell):
        return
    if src.has_style:
        dst._style = copy(src._style)


def column_width(ws, idx: int):
    """Explicit width of a column (handles grouped <col min= max=> ranges); None = default width."""
    for dim in ws.column_dimensions.values():
        lo, hi = dim.min or 0, dim.max or 0
        if lo <= idx <= hi and dim.width and (dim.customWidth or dim.width):
            return dim.width
    return None


def set_column_width(ws, idx: int, width) -> None:
    letter = get_column_letter(idx)
    dim = ws.column_dimensions[letter]
    dim.min = dim.max = idx
    if width:
        dim.width = width


def header_map(ws, header_row: int = 1) -> dict:
    out = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(header_row, c).value
        if v is not None and str(v).strip():
            out.setdefault(str(v).strip(), c)
    return out


def section_sort_key(name):
    """Natural order for section names: 1, 4, 12 ... and 901, 902 (text names after numbers)."""
    s = str(name).strip()
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def is_section_sheet(name: str) -> bool:
    return name.strip().lower() != ALL_SECTIONS.lower()


# ----------------------------------------------------------------------------- sheet profile
@dataclass
class SheetProfile:
    """Formatting fingerprint of a template worksheet, used to build new sheets/columns/rows."""
    fixed_headers: list = field(default_factory=list)
    header_height: float | None = None
    data_height: float | None = None
    header_style: dict = field(default_factory=dict)
    data_style: dict = field(default_factory=dict)
    width: dict = field(default_factory=dict)
    fixed_width: dict = field(default_factory=dict)       # header name -> width (fixed columns)
    default_row_height: float | None = None
    auto_filter: str | None = None
    freeze: str | None = None
    zoom: int | None = None

    @classmethod
    def from_sheet(cls, ws) -> "SheetProfile":
        p = cls()
        hmap = header_map(ws)
        p.header_height = ws.row_dimensions[1].height
        p.data_height = ws.row_dimensions[2].height
        p.default_row_height = ws.sheet_format.defaultRowHeight
        p.auto_filter = ws.auto_filter.ref
        p.freeze = ws.freeze_panes
        p.zoom = ws.sheet_view.zoomScale
        for h, c in hmap.items():
            k = classify(h)
            if k in FIXED_KINDS:
                p.fixed_headers.append(h)
                p.fixed_width[h] = column_width(ws, c)
            p.header_style.setdefault(k, CellStyle.of(ws.cell(1, c)))
            p.data_style.setdefault(k, CellStyle.of(ws.cell(2, c)))
            p.width.setdefault(k, column_width(ws, c))
        # Sensible fallbacks: every kind can borrow from the closest existing kind
        fallback = {"mark": "score", "total": "score", "feedback": "score", "score": "mark",
                    "section": "id", "department": "name", "name": "id"}
        for k, src in fallback.items():
            if k not in p.header_style and src in p.header_style:
                p.header_style[k] = p.header_style[src]
                p.data_style[k] = p.data_style[src]
                p.width.setdefault(k, p.width.get(src))
        anyk = next(iter(p.header_style), None)
        for k in ("id", "name", "section", "department", "score", "feedback", "mark", "total"):
            if k not in p.header_style and anyk:
                p.header_style[k] = p.header_style[anyk]
                p.data_style[k] = p.data_style[anyk]
                p.width.setdefault(k, None)
        p.data_style["id"].number_format = "@"
        return p

    def width_for(self, header: str):
        if header in self.fixed_width:
            return self.fixed_width[header]
        return self.width.get(classify(header))


def _profiles(template_key: str) -> tuple[SheetProfile, SheetProfile | None, str]:
    tpl = template_path(template_key)
    wb = openpyxl.load_workbook(tpl)
    sec_ws = next((ws for ws in wb.worksheets if is_section_sheet(ws.title)), wb.worksheets[0])
    all_ws = next((ws for ws in wb.worksheets if not is_section_sheet(ws.title)), None)
    return SheetProfile.from_sheet(sec_ws), (SheetProfile.from_sheet(all_ws) if all_ws else None), tpl


# ----------------------------------------------------------------------------- templates
def template_path(key: str) -> str:
    p = path(TEMPLATE_DIR, TEMPLATES[key])
    if not os.path.exists(p):
        ensure_templates()
    if not os.path.exists(p):
        raise FileNotFoundError(f"Template workbook missing: {TEMPLATE_DIR}/{TEMPLATES[key]}. "
                                f"Copy a formatted base workbook there (e.g. {TEMPLATE_SOURCES[key]}).")
    return p


def ensure_templates(force: bool = False) -> list[str]:
    """Create templates/*.xlsx from the base workbooks: header row + one formatted empty data row
    per sheet. Formatting is kept exactly; student data is removed."""
    made = []
    os.makedirs(path(TEMPLATE_DIR), exist_ok=True)
    for key, name in TEMPLATES.items():
        dst = path(TEMPLATE_DIR, name)
        src = path(TEMPLATE_SOURCES[key])
        if (os.path.exists(dst) and not force) or not os.path.exists(src):
            continue
        wb = openpyxl.load_workbook(src)
        for ws in wb.worksheets:
            for rng in list(ws.merged_cells.ranges):
                if rng.min_row >= 2:
                    ws.unmerge_cells(str(rng))
            if ws.max_row > 2:
                ws.delete_rows(3, ws.max_row - 2)
            for c in range(1, ws.max_column + 1):
                cell = ws.cell(2, c)
                if not isinstance(cell, MergedCell):
                    cell.value = None
        wb.save(dst)
        made.append(dst)
    return made


# ----------------------------------------------------------------------------- headers per workbook
def dynamic_headers(kind: str, course: dict, comp: dict | None = None) -> list[str]:
    if kind == "attendance":
        return [f"Week #{i}" for i in range(1, course["weeks"] + 1)] + ["Total Attendance"]
    if kind == "grades":
        q = cfgmod.quiz_component(course)
        n = q["count"] if q else 0
        out = []
        for i in range(1, n + 1):
            out += [f"Quiz #{i}", f"Quiz #{i} FB"]
        return out
    return cfgmod.workbook_columns(comp)


def total_formula(row: int, first_col: int, last_col: int, weeks: int) -> str:
    a, b = get_column_letter(first_col), get_column_letter(last_col)
    return f'=COUNTIF({a}{row}:{b}{row}, "+") & "/{weeks}"'


def _student_fixed_value(header: str, st: dict, section: str):
    k = classify(header)
    if k == "id":
        return clean_id(st.get(ID_HEADER))
    if k == "name":
        return st.get(NAME_HEADER) or ""
    if k == "section":
        return section
    if k == "department":
        return st.get(DEPT_HEADER) or ""
    return None


def _apply_sheet_props(ws, prof: SheetProfile, n_cols: int) -> None:
    if prof.default_row_height:
        ws.sheet_format.defaultRowHeight = prof.default_row_height
    if prof.zoom:
        ws.sheet_view.zoomScale = prof.zoom
    if prof.freeze:
        ws.freeze_panes = prof.freeze
    if prof.auto_filter:
        ws.auto_filter.ref = prof.auto_filter


def _build_sheet(ws, prof: SheetProfile, fixed: list[str], dyn: list[str], students: list[dict],
                 section_of, kind: str, weeks: int) -> None:
    headers = fixed + dyn
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(1, c, h)
        prof.header_style[classify(h)].apply(cell)
        set_column_width(ws, c, prof.width_for(h))
    if prof.header_height:
        ws.row_dimensions[1].height = prof.header_height
    first_mark = next((i for i, h in enumerate(headers, 1) if classify(h) == "mark"), None)
    last_mark = max((i for i, h in enumerate(headers, 1) if classify(h) == "mark"), default=None)
    for r, st in enumerate(students, start=2):
        for c, h in enumerate(headers, start=1):
            k = classify(h)
            cell = ws.cell(r, c)
            prof.data_style[k].apply(cell)
            if k in FIXED_KINDS:
                cell.value = _student_fixed_value(h, st, section_of(st))
            elif k == "total" and first_mark:
                cell.value = total_formula(r, first_mark, last_mark, weeks)
        if prof.data_height:
            ws.row_dimensions[r].height = prof.data_height
    _apply_sheet_props(ws, prof, len(headers))


def build_workbook(dest: str, kind: str, course: dict, students: list[dict], comp: dict | None = None,
                   overwrite: bool = False) -> str:
    """Create a course-coded workbook (attendance / grades / component) from the template.

    students: [{"Student ID", "Full Name", "Section", "Department"}]
    """
    dest_abs = dest if os.path.isabs(dest) else path(dest)
    ensure_writable(dest_abs)
    if os.path.exists(dest_abs):
        if not overwrite:
            raise FileExistsError(f"{os.path.basename(dest_abs)} already exists. Use roster sync to update it.")
        bk.backup_file(dest_abs, term=course.get("term"), course=course["code"])
    tkey = "attendance" if kind == "attendance" else ("grades" if kind == "grades" else "component")
    sec_prof, all_prof, tpl = _profiles(tkey)
    wb = openpyxl.load_workbook(tpl)           # keeps theme, default fonts, workbook properties
    old = list(wb.worksheets)
    for i, ws in enumerate(old):          # free the names "901", "All Sections", ...
        ws.title = f"__template_{i}"
    dyn = dynamic_headers(kind, course, comp)
    sections = list(course["sections"])
    extra = sorted({clean_section(s.get(SECTION_HEADER)) for s in students} - set(sections) - {""},
                   key=section_sort_key)
    sections = sorted(set(sections + extra), key=section_sort_key)
    for sec in sections:
        ws = wb.create_sheet(sec)
        rows = sorted([s for s in students if clean_section(s.get(SECTION_HEADER)) == sec],
                      key=lambda s: clean_id(s.get(ID_HEADER)))
        _build_sheet(ws, sec_prof, sec_prof.fixed_headers, dyn, rows, lambda s, _sec=sec: _sec, kind,
                     course["weeks"])
    if all_prof is not None:
        ws = wb.create_sheet(ALL_SECTIONS)
        fixed = list(all_prof.fixed_headers)
        if SECTION_HEADER not in fixed:
            fixed.insert(min(2, len(fixed)), SECTION_HEADER)
        rows = sorted(students, key=lambda s: (sections.index(clean_section(s.get(SECTION_HEADER)))
                                               if clean_section(s.get(SECTION_HEADER)) in sections else 99,
                                               clean_id(s.get(ID_HEADER))))
        _build_sheet(ws, all_prof, fixed, dyn, rows, lambda s: clean_section(s.get(SECTION_HEADER)), kind,
                     course["weeks"])
    for ws in old:
        wb.remove(ws)
    wb.save(dest_abs)
    return dest_abs


# ----------------------------------------------------------------------------- row operations
def id_rows(ws, id_col: int) -> dict:
    """{student_id: row} for rows that hold a real student ID (>= 5 digits)."""
    out = {}
    for r in range(2, ws.max_row + 1):
        sid = clean_id(ws.cell(r, id_col).value)
        if len(sid) >= 5 and sid not in out:
            out[sid] = r
    return out


def _row_in_merge(ws, r: int) -> bool:
    return any(rng.min_row <= r <= rng.max_row for rng in ws.merged_cells.ranges)


def _row_empty(ws, r: int, ncols: int) -> bool:
    return all(is_blank(ws.cell(r, c).value) for c in range(1, ncols + 1))


def _shift_merges(ws, from_row: int, delta: int) -> None:
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= from_row:
            ws.merged_cells.remove(rng)
            rng.shift(row_shift=delta)
            ws.merged_cells.add(rng)


def _translate_row_formulas(ws, rows, delta: int) -> None:
    """After rows moved by `delta`, rewrite same-sheet relative formulas so they point to their new row."""
    for r in rows:
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(r, c)
            v = cell.value
            if isinstance(v, str) and v.startswith("=") and "!" not in v:
                col = get_column_letter(c)
                try:
                    cell.value = Translator(v, origin=f"{col}{r - delta}").translate_formula(f"{col}{r}")
                except Exception:
                    pass


def insert_row(ws, r: int) -> None:
    ws.insert_rows(r)
    _shift_merges(ws, r, 1)
    _translate_row_formulas(ws, range(r + 1, ws.max_row + 1), 1)


def delete_row(ws, r: int) -> None:
    ws.delete_rows(r)
    _shift_merges(ws, r + 1, -1)
    _translate_row_formulas(ws, range(r, ws.max_row + 1), -1)


def append_student(ws, values: dict, ncols: int | None = None) -> int:
    """Add a student after the last ID row, cloning the style and height of the last student row
    (falls back to the template look when the sheet has no students). Returns the new row."""
    hmap = header_map(ws)
    id_col = hmap.get(ID_HEADER, 1)
    ncols = ncols or ws.max_column
    rows = id_rows(ws, id_col)
    last = max(rows.values()) if rows else 1
    new = last + 1
    if not _row_empty(ws, new, ncols) or _row_in_merge(ws, new):
        insert_row(ws, new)
    src_row = last if rows else None
    for c in range(1, ncols + 1):
        cell = ws.cell(new, c)
        if src_row:
            src = ws.cell(src_row, c)
            copy_cell_style(src, cell)
            if isinstance(src.value, str) and src.value.startswith("=") and "!" not in src.value:
                col = get_column_letter(c)
                cell.value = Translator(src.value, origin=f"{col}{src_row}").translate_formula(f"{col}{new}")
    if src_row and ws.row_dimensions[src_row].height:
        ws.row_dimensions[new].height = ws.row_dimensions[src_row].height
    for h, c in hmap.items():
        if h in values:
            v = values[h]
            cell = ws.cell(new, c)
            if classify(h) == "id":
                v = clean_id(v)
                cell.number_format = "@"
            cell.value = v
    # A student row without a source row (empty sheet) still needs its attendance total formula
    marks = [c for h, c in hmap.items() if classify(h) == "mark"]
    for h, c in hmap.items():
        if classify(h) == "total" and marks and is_blank(ws.cell(new, c).value):
            ws.cell(new, c).value = total_formula(new, min(marks), max(marks), len(marks))
    return new


def ensure_column(ws, header: str, profile: SheetProfile | None = None) -> int:
    """Return the column of `header`; create it (cloning a same-kind column's look) if missing."""
    hmap = header_map(ws)
    if header in hmap:
        return hmap[header]
    k = classify(header)
    new = max(hmap.values(), default=0) + 1
    same = [c for h, c in hmap.items() if classify(h) == k]
    src = same[-1] if same else (max(hmap.values()) if hmap else None)
    ws.cell(1, new, header)
    if src:
        for r in range(1, ws.max_row + 1):
            copy_cell_style(ws.cell(r, src), ws.cell(r, new))
        set_column_width(ws, new, column_width(ws, src))
    elif profile:
        profile.header_style[k].apply(ws.cell(1, new))
        set_column_width(ws, new, profile.width.get(k))
    return new


# ----------------------------------------------------------------------------- All Sections links
def relink_all_sections(wb) -> int:
    """Re-point "All Sections" formulas such as ='901'!F3 to the row/section where each student now lives.
    Needed after students are added, moved or removed in section sheets. Returns formulas changed."""
    if ALL_SECTIONS not in wb.sheetnames:
        return 0
    index, where = {}, {}
    for ws in wb.worksheets:
        if not is_section_sheet(ws.title):
            continue
        hm = header_map(ws)
        if ID_HEADER not in hm:
            continue
        rows = id_rows(ws, hm[ID_HEADER])
        index[ws.title] = rows
        for sid in rows:
            where.setdefault(sid, ws.title)
    ws = wb[ALL_SECTIONS]
    hm = header_map(ws)
    if ID_HEADER not in hm:
        return 0
    changed = 0
    for r in range(2, ws.max_row + 1):
        sid = clean_id(ws.cell(r, hm[ID_HEADER]).value)
        if sid not in where:
            continue
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(r, c)
            v = cell.value
            if not (isinstance(v, str) and v.startswith("=") and "!" in v):
                continue

            def _fix(m):
                sheet = where[sid]
                target = index[sheet][sid]
                sname = f"'{sheet}'"
                return f"{sname}!{m.group('col')}{target}"
            nv = _FORMULA_SHEET_REF.sub(_fix, v)
            if nv != v:
                cell.value = nv
                changed += 1
    return changed


# ----------------------------------------------------------------------------- value writes
PLACEHOLDERS = (None, "", "-", "Absent from class.", "Absent.", "Absent", "Absent - 0 points.",
                "Absent - 0 points")


def write_values(file_path: str, column: str, data: dict, soft_keys=None, create_column: bool = True,
                 term: str | None = None, course: str | None = None, do_backup: bool = True) -> dict:
    """Write {student_id: value} into `column` of every sheet that lists the student.

    * Only values change; formatting stays as it is.
    * soft_keys: for these students an existing non-placeholder value is NOT overwritten
      (e.g. a student who attended another section that week).
    * Cells holding cross-sheet formulas (All Sections mirrors) are left untouched.
    Returns {"cells": n, "students": set(ids), "sheets": [...]}.
    """
    return write_many(file_path, {column: data}, soft_keys={column: soft_keys or ()},
                      create_column=create_column, term=term, course=course, do_backup=do_backup)


def write_many(file_path: str, columns: dict, soft_keys: dict | None = None, create_column: bool = True,
               term: str | None = None, course: str | None = None, do_backup: bool = True) -> dict:
    """Several columns at once: {column: {student_id: value}}, one backup and one save.
    soft_keys: {column: ids} (see write_values). Returns {"cells", "students", "sheets"}."""
    p = file_path if os.path.isabs(file_path) else path(file_path)
    result = {"cells": 0, "students": set(), "sheets": []}
    columns = {c: d for c, d in columns.items() if d}
    if not os.path.exists(p) or not columns:
        return result
    soft_keys = {c: set(v or ()) for c, v in (soft_keys or {}).items()}
    ensure_writable(p)
    if do_backup:
        bk.backup_file(p, term=term, course=course)
    wb = openpyxl.load_workbook(p)
    for ws in wb.worksheets:
        hm = header_map(ws)
        if ID_HEADER not in hm:
            continue
        rows = id_rows(ws, hm[ID_HEADER])
        touched = False
        for column, data in columns.items():
            if not any(sid in rows for sid in data):
                continue
            col = hm.get(column) or (ensure_column(ws, column) if create_column else None)
            if not col:
                continue
            hm[column] = col
            soft = soft_keys.get(column, set())
            for sid, val in data.items():
                r = rows.get(sid)
                if not r:
                    continue
                cell = ws.cell(r, col)
                if isinstance(cell, MergedCell):
                    continue
                cur = cell.value
                if isinstance(cur, str) and cur.startswith("=") and "!" in cur:
                    continue
                if sid in soft and cur not in PLACEHOLDERS:
                    continue
                if is_blank(cur) and is_blank(val):
                    continue
                if cur != val:
                    cell.value = val
                    result["cells"] += 1
                    result["students"].add(sid)
                    touched = True
        if touched:
            result["sheets"].append(ws.title)
    wb.save(p)
    return result


# ----------------------------------------------------------------------------- reading
def read_students(file_path: str) -> list[dict]:
    """All students listed in a course workbook: [{"Student ID", "Full Name", "Section", "Department",
    "_sheet", "_row"}]. Section sheets win; "All Sections" fills in students missing there."""
    p = file_path if os.path.isabs(file_path) else path(file_path)
    if not os.path.exists(p):
        return []
    wb = openpyxl.load_workbook(p, data_only=True, read_only=False)
    seen, out = set(), []
    sheets = sorted(wb.worksheets, key=lambda w: 0 if is_section_sheet(w.title) else 1)
    for ws in sheets:
        hm = header_map(ws)
        if ID_HEADER not in hm:
            continue
        for sid, r in id_rows(ws, hm[ID_HEADER]).items():
            if sid in seen:
                continue
            seen.add(sid)
            sec = ws.title.strip() if is_section_sheet(ws.title) else clean_section(
                ws.cell(r, hm[SECTION_HEADER]).value if SECTION_HEADER in hm else "")
            out.append({
                ID_HEADER: sid,
                NAME_HEADER: str(ws.cell(r, hm[NAME_HEADER]).value or "").strip() if NAME_HEADER in hm else "",
                SECTION_HEADER: sec,
                DEPT_HEADER: str(ws.cell(r, hm[DEPT_HEADER]).value or "").strip() if DEPT_HEADER in hm else "",
                "_sheet": ws.title, "_row": r,
            })
    return out


def read_table(file_path: str):
    """Workbook as a pandas DataFrame (one row per student, all score columns).

    Section sheets are read first (they hold the real values); "All Sections" only fills gaps.
    This avoids reading formula cells whose cached value was dropped by a script save."""
    import pandas as pd
    p = file_path if os.path.isabs(file_path) else path(file_path)
    if not os.path.exists(p):
        return pd.DataFrame()
    wb = openpyxl.load_workbook(p, data_only=True)
    rec: dict[str, dict] = {}
    sheets = sorted(wb.worksheets, key=lambda w: 0 if is_section_sheet(w.title) else 1)
    for ws in sheets:
        hm = header_map(ws)
        if ID_HEADER not in hm:
            continue
        is_sec = is_section_sheet(ws.title)
        for sid, r in id_rows(ws, hm[ID_HEADER]).items():
            row = rec.setdefault(sid, {ID_HEADER: sid})
            if is_sec and SECTION_HEADER not in row:
                row[SECTION_HEADER] = ws.title.strip()
            for h, c in hm.items():
                if h == ID_HEADER:
                    continue
                v = ws.cell(r, c).value
                if h == SECTION_HEADER:
                    v = clean_section(v)
                if is_blank(row.get(h)) and not is_blank(v):
                    row[h] = v
    df = pd.DataFrame(list(rec.values()))
    if not df.empty:
        df[ID_HEADER] = df[ID_HEADER].astype(str)
    return df
