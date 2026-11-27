"""Give 0 to absent students who have no score for a held Quiz or Experiment.

    python sync_absent_zeros.py            # preview only, nothing is written
    python sync_absent_zeros.py --apply    # write the zeros (lock check + stamped backups first)

The same action is available in the Local Desktop UI: Overview -> "Sync Absent Zeros".
Nothing is pushed to GitHub; publish afterwards from the desktop UI with the pre-push preview.
"""
from __future__ import annotations

import sys

from qs_core import absent_zeros as az
from qs_core import config as cfgmod


def main(argv: list[str]) -> int:
    cfg = cfgmod.load()
    changes = az.plan(cfg)
    if not changes:
        print("Absent zeros are in sync: no absent student is missing a 0.")
        return 0
    print(f"{len(changes)} absent record(s) without a score:")
    for row in az.summarize(changes):
        print(f"  {row['Course']}  Section {row['Section']}  {row['Item']:<8} {row['Week']:<8} {row['Zeros']:>3}")
    print("Files: " + ", ".join(az.files_of(changes)))
    if "--apply" not in argv:
        print("Preview only. Run again with --apply to write the zeros.")
        return 0
    try:
        out = az.apply(cfg, changes)
    except ValueError as e:
        print(f"Stopped: {e}")
        return 1
    for line in out["log"]:
        print(line)
    print(f"Done: {out['zeros']} record(s) now have 0. Stamped backups are in backups/. Nothing was pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
