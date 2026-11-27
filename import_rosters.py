"""Start-of-semester roster import from the university grade-report exports (same engine as the dashboard).

    python import_rosters.py                         # preview the files found in this folder
    python import_rosters.py --apply                 # create/update every course found
    python import_rosters.py --apply --course PHY1001 --group "Lab Section"
    python import_rosters.py path/to/report.xlsx --apply

Files: "Öğretim Elemanı Not Excel Raporu.xlsx", "... (1).xlsx", "... (2).xlsx", ... in this folder or input/rosters/.
"""
from __future__ import annotations

import argparse
import os
import sys

from qs_core import config as cfgmod
from qs_core import excel_io as xio
from qs_core import semester_import as semi
from qs_core.common import BASE_DIR

os.chdir(BASE_DIR)


def main() -> int:
    ap = argparse.ArgumentParser(description="Import the university grade-report rosters.")
    ap.add_argument("files", nargs="*", help="report files (default: search this folder and input/rosters/)")
    ap.add_argument("--apply", action="store_true", help="write the workbooks (otherwise preview only)")
    ap.add_argument("--course", action="append", help="only this course code (repeatable)")
    ap.add_argument("--group", default="Section", choices=["Section", "Lab Section"])
    ap.add_argument("--term", default=None)
    ap.add_argument("--weeks", type=int, default=14)
    ap.add_argument("--quizzes", type=int, default=9)
    ap.add_argument("--remove-dropped", action="store_true")
    a = ap.parse_args()

    cfg = cfgmod.ensure_file()
    xio.ensure_templates()
    files = [os.path.abspath(f) for f in a.files] or semi.find_report_files()
    if not files:
        print("No grade-report files found. Put them in this folder or in input/rosters/.")
        return 1
    res = semi.read_report_files(files, a.group)
    for name, n, codes in res.files:
        print(f"📄 {name}: {n} student row(s), course(s) {', '.join(codes) or 'none'}")
    for w in res.warnings:
        print(f"⚠️ {w}")
    if not res.courses:
        return 1
    print("\nCourse preview (grouped by '%s'):" % a.group)
    for r in semi.preview(cfg, res):
        print(f"  {r['Course']} · {r['Title']} · {r['Students']} students · sheets: {r['Sections (sheets)']}")
        print(f"      {r['Status']}: {r['Changes']}")
    codes = [c.upper() for c in (a.course or res.courses.keys())]
    missing = [c for c in codes if c not in res.courses]
    if missing:
        print(f"❌ Not found in the files: {', '.join(missing)}")
        return 1
    if not a.apply:
        print("\nPreview only. Add --apply to create/update the workbooks.")
        return 0
    term = a.term or cfg.get("active_term")
    for line in semi.apply(cfg, res, codes, term, a.group, a.weeks, a.quizzes, a.remove_dropped):
        print("✅ " + line)
    print("\nPublish the portal files with:  python publish.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
