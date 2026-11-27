"""Update the course workbooks from grades_database.xlsx (the database is the source of truth).

    python sync_workbooks.py              # preview only, nothing is written
    python sync_workbooks.py --apply      # write (open-Excel check + stamped backups first)
    python sync_workbooks.py --apply --keep-extra
                                          # fill/update only; never clear a workbook value

The same action is in the Local Desktop UI: Overview or Archive, Backups & Publish -> Workbook sync.
grades_database.xlsx is only read. The course workbooks are local only, so nothing is pushed.
"""
from __future__ import annotations

import sys

from qs_core import config as cfgmod
from qs_core import db_sync


def main(argv: list[str]) -> int:
    cfg = cfgmod.load()
    plan = db_sync.plan(cfg, clear_missing="--keep-extra" not in argv)
    for s in plan["skipped"]:
        print(f"Skipped: {s['Course']} {s['Item']} {s['Student ID']}: {s['Reason']}")
    if not plan["changes"]:
        print("The course workbooks already match grades_database.xlsx.")
        return 0
    print(f"{len(plan['changes'])} workbook cell(s) differ from grades_database.xlsx:")
    for r in db_sync.summarize(plan["changes"]):
        print(f"  {r['Course']}  {r['File']:<26} {r['Change']:<40} {r['Cells']:>4}")
    if "--apply" not in argv:
        print("Preview only. Run again with --apply to update the workbooks.")
        return 0
    try:
        out = db_sync.apply(cfg, plan["changes"])
    except ValueError as e:
        print(f"Stopped: {e}")
        return 1
    for line in out["log"]:
        print(line)
    print(f"Done: {out['cells']} cell(s) updated. Stamped backups are in backups/. grades_database.xlsx was not "
          f"changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
