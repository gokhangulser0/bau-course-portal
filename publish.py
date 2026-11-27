"""Publish portal data (and optionally code) to GitHub with a pre-push summary.

    python publish.py                       # all portal data files that changed
    python publish.py MCH2008_homework.xlsx # specific files
    python publish.py --code                # data + application code
    python publish.py -m "Midterm grades"   # custom commit message
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from qs_core import gitops
from qs_core import config as cfgmod
from qs_core.common import BASE_DIR, path

os.chdir(BASE_DIR)


def run(files=None, message=None, include_code=False, assume_yes=False) -> int:
    targets = list(files or gitops.publishable_files(cfgmod.load()))
    if include_code:
        targets += [p for p in gitops.CODE_PATHS if os.path.exists(path(p))]
    missing = [f for f in targets if not os.path.exists(path(f))]
    if missing:
        print(f"❌ File(s) not found: {', '.join(missing)}")
        return 1
    summary = gitops.pre_push_summary(targets)
    if not summary:
        print("ℹ️ No unpublished changes (the files are already up to date).")
        return 0
    print("📦 Pre-push summary")
    for item in summary:
        print(f"   {item['file']} ({item['status']})")
        for d in item["details"]:
            print(f"      {d}")
    msg = message or f"Portal update ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
    if not assume_yes and input(f"\nCommit & push with message '{msg}'? [y/N]: ").strip().lower() != "y":
        print("Cancelled.")
        return 1
    res = gitops.publish([i["file"] for i in summary] if not include_code else targets, msg)
    for line in res["log"]:
        print(("✅ " if res["ok"] else "❌ ") + line)
    return 0 if res["ok"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Commit and push portal files to GitHub.")
    ap.add_argument("files", nargs="*")
    ap.add_argument("-m", "--message")
    ap.add_argument("--code", action="store_true", help="also publish the application code")
    ap.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args()
    return run(a.files, a.message, a.code, a.yes)


if __name__ == "__main__":
    sys.exit(main())
