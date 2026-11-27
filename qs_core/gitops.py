"""Single shared helper for publishing to GitHub (replaces the copy-pasted commit/push logic in
the old push_* helper scripts and process_week.py).

publish() works in one of three modes:
  git         local clone (instructor PC): git add / commit / pull --rebase / push (never --force)
  github_api  Streamlit Cloud: files are committed one by one through the GitHub Contents API
  none        nothing configured: changes stay local
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess

from . import config as cfgmod
from .common import (ANNOUNCEMENTS_FILE, BASE_DIR, CONFIG_FILE, DB_FILE, META_FILE, STATUS_FILE, clean_id,
                     path)

CODE_PATHS = ["app.py", "portal_logic.py", "qs_core", "ui", "requirements.txt", ".gitignore", "templates",
              "publish.py", "import_rosters.py",
              "run_lan.bat", "run_lan.ps1", "run_lan.sh", "desktop_app.py",
              "run_desktop.bat", "run_desktop.sh"]


def git(args, check=False):
    return subprocess.run(["git"] + list(args), cwd=BASE_DIR, check=check, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def repo_available() -> bool:
    try:
        r = git(["rev-parse", "--is-inside-work-tree"])
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, FileNotFoundError):
        return False


def current_branch() -> str:
    r = git(["rev-parse", "--abbrev-ref", "HEAD"])
    b = r.stdout.strip() if r.returncode == 0 else ""
    return b if b and b != "HEAD" else "main"


def excel_locked(rel: str) -> bool:
    from .common import is_locked
    return is_locked(rel)


def publishable_files(cfg: dict | None = None) -> list[str]:
    """Data files the student portal reads."""
    cfg = cfg or cfgmod.load()
    files = [DB_FILE, META_FILE, STATUS_FILE, ANNOUNCEMENTS_FILE, CONFIG_FILE, "portal_updates.json"]
    for c in cfg["courses"].values():
        for f in cfgmod.published_course_files(c):
            if f not in files:
                files.append(f)
    return [f for f in files if os.path.exists(path(f))]


# ----------------------------------------------------------------------------- pre-push summary
def pending_changes(paths=None) -> list[tuple[str, str]]:
    """[(status, file)] from git status (M = modified, ?? = new, D = deleted)."""
    if not repo_available():
        return []
    r = git(["status", "--porcelain", "--untracked-files=all", "--"] + list(paths or []))
    out = []
    for line in r.stdout.splitlines():
        if line.strip():
            out.append((line[:2].strip() or "M", line[3:].strip().strip('"')))
    return out


def head_bytes(rel: str) -> bytes | None:
    try:
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=BASE_DIR, capture_output=True)
        return r.stdout if r.returncode == 0 else None
    except (OSError, FileNotFoundError):
        return None


def _new_aff() -> dict:
    return {"courses": set(), "weeks": set(), "sections": set(), "items": set(), "records": 0}


def _db_diff(old: bytes | None, new_path: str, aff: dict | None = None) -> list[str]:
    import pandas as pd
    aff = aff if aff is not None else _new_aff()
    new = pd.read_excel(new_path, dtype={"student_id": str})
    if old is None:
        aff["courses"] |= set(new["course_code"].dropna().astype(str))
        aff["records"] += len(new)
        return [f"{len(new)} portal record(s) (new file)."]
    old_df = pd.read_excel(io.BytesIO(old), dtype={"student_id": str})
    key = ["course_code", "student_id", "week", "quiz_no"]
    vals = ["attendance", "quiz_score", "feedback", "section", "attended_section"]
    for d in (old_df, new):
        d["student_id"] = d["student_id"].map(clean_id)
        for c in key + vals:
            if c not in d.columns:
                d[c] = ""
            d[c] = d[c].astype(str).replace({"nan": "", "None": ""})
    o = old_df.drop_duplicates(key).set_index(key)[vals]
    n = new.drop_duplicates(key).set_index(key)[vals]
    added = n.index.difference(o.index)
    removed = o.index.difference(n.index)
    common = n.index.intersection(o.index)
    changed = [ix for ix in common if not o.loc[ix].equals(n.loc[ix])]
    lines = [f"{len(added)} record(s) added, {len(changed)} updated, {len(removed)} removed."]
    groups = {}
    for ix in list(added) + changed:
        groups.setdefault((ix[0], ix[2], ix[3]), 0)
        groups[(ix[0], ix[2], ix[3])] += 1
    for (c, w, q), cnt in sorted(groups.items()):
        lines.append(f"  {c} · {w} · {q}: {cnt} student record(s)")
        aff["courses"].add(c)
        aff["weeks"].add(w)
        if q and q not in ("No Quiz", ""):
            aff["items"].add(q)
        else:
            aff["items"].add("Attendance")
    aff["records"] += len(added) + len(changed) + len(removed)
    for ix in list(added) + changed:
        try:
            sec = str(n.loc[ix, "section"]).replace(".0", "")
            if sec:
                aff["sections"].add(sec)
        except Exception:
            pass
    try:
        from . import dbio
        o_ros = pd.read_excel(io.BytesIO(old), sheet_name=dbio.ROSTER_SHEET, dtype=str) \
            if dbio.ROSTER_SHEET in pd.ExcelFile(io.BytesIO(old)).sheet_names else pd.DataFrame(columns=["course_code"])
        n_ros = dbio.read_roster(new_path)
        oc, nc = o_ros.groupby("course_code").size().to_dict(), n_ros.groupby("course_code").size().to_dict()
        for code in sorted(set(oc) | set(nc)):
            if oc.get(code, 0) != nc.get(code, 0):
                lines.append(f"  Roster sheet · {code}: {oc.get(code, 0)} -> {nc.get(code, 0)} student(s)")
                aff["courses"].add(code)
                aff["items"].add("Roster")
    except Exception:
        pass
    return lines


_WEEK_COL = re.compile(r"^Week\s*#?\s*(\d+)$", re.I)


def _xlsx_diff(old: bytes | None, new_path: str, aff: dict | None = None) -> list[str]:
    import openpyxl
    from . import excel_io as xio
    aff = aff if aff is not None else _new_aff()
    new_wb = openpyxl.load_workbook(new_path, data_only=False)
    if old is None:
        aff["sections"] |= {w.title for w in new_wb.worksheets if xio.is_section_sheet(w.title)}
        aff["items"].add("New workbook")
        return [f"New workbook with sheets: {', '.join(new_wb.sheetnames)}."]
    old_wb = openpyxl.load_workbook(io.BytesIO(old), data_only=False)
    lines = []
    all_students = set()
    for ws in new_wb.worksheets:
        if ws.title not in old_wb.sheetnames:
            lines.append(f"  [{ws.title}] new sheet")
            if xio.is_section_sheet(ws.title):
                aff["sections"].add(ws.title)
            continue
        ow = old_wb[ws.title]
        hm = xio.header_map(ws)
        idc = hm.get(xio.ID_HEADER, 1)
        cells, students, cols = 0, set(), set()
        for r in range(1, max(ws.max_row, ow.max_row) + 1):
            for c in range(1, max(ws.max_column, ow.max_column) + 1):
                if ws.cell(r, c).value != ow.cell(r, c).value:
                    cells += 1
                    sid = clean_id(ws.cell(r, idc).value)
                    if sid:
                        students.add(sid)
                    h = ws.cell(1, c).value
                    if h:
                        cols.add(str(h))
        if cells:
            lines.append(f"  [{ws.title}] {cells} cell(s), {len(students)} student(s); columns: "
                         f"{', '.join(sorted(cols)[:8])}{' ...' if len(cols) > 8 else ''}")
            if xio.is_section_sheet(ws.title):
                aff["sections"].add(ws.title)
            all_students |= students
            for col in cols:
                m = _WEEK_COL.match(col)
                if m:
                    aff["weeks"].add(f"Week {int(m.group(1))}")
                    aff["items"].add("Attendance")
                elif col.endswith(" FB"):
                    aff["items"].add(col[:-3] + " (feedback)")
                elif xio.classify(col) in ("score",):
                    aff["items"].add(col)
    aff["records"] += len(all_students)
    return lines or ["  No cell values changed (formatting or metadata only)."]


def _json_affected(f: str, old: bytes | None, new: bytes, aff: dict) -> None:
    import json
    try:
        o = json.loads(old.decode("utf-8-sig")) if old else {}
        n = json.loads(new.decode("utf-8-sig"))
    except Exception:
        return
    if f == ANNOUNCEMENTS_FILE:
        oa = {a["id"]: a for a in (o.get("announcements", []) if isinstance(o, dict) else o)}
        na = {a["id"]: a for a in (n.get("announcements", []) if isinstance(n, dict) else n)}
        changed = [na[i] for i in na if oa.get(i) != na[i]] + [oa[i] for i in oa if i not in na]
        for a in changed:
            aff["courses"].add(a.get("course") or "All courses")
            aff["sections"] |= set(a.get("sections") or [])
            aff["items"].add(f"Announcement: {a.get('title', '')[:40]}")
        aff["records"] += len(changed)
    elif f == CONFIG_FILE:
        oc, nc = (o.get("courses") or {}), (n.get("courses") or {})
        for code in set(oc) | set(nc):
            if oc.get(code) != nc.get(code):
                aff["courses"].add(code)
                aff["items"].add("Course setup / criteria")
        if o.get("active_term") != n.get("active_term") or o.get("terms") != n.get("terms"):
            aff["items"].add("Terms")


def _csv_affected(old: bytes | None, new: bytes, aff: dict) -> None:
    ol = set((old or b"").decode("utf-8-sig", "ignore").splitlines()[1:])
    nl = set(new.decode("utf-8-sig", "ignore").splitlines()[1:])
    for line in (nl ^ ol):
        parts = line.split(",")
        if len(parts) >= 3:
            aff["courses"].add(parts[0])
            aff["weeks"].add(f"Week {parts[1]}")
            aff["sections"].add(parts[2])
            aff["items"].add("No Class entry")
            aff["records"] += 1


def _sort_weeks(ws):
    return sorted(ws, key=lambda w: int(re.sub(r"\D", "", w) or 0))


def pre_push_summary(paths) -> list[dict]:
    """[{"file", "status", "details": [lines], "courses", "weeks", "sections", "items", "records"}]
    for the files that changed since the last commit."""
    out = []
    cfg = cfgmod.load()
    changes = pending_changes(paths) if repo_available() else [("?", p) for p in paths]
    for st, f in changes:
        p = path(f)
        details = []
        aff = _new_aff()
        try:
            if f == DB_FILE and os.path.exists(p):
                details = _db_diff(head_bytes(f), p, aff)
            elif f.endswith(".xlsx") and os.path.exists(p):
                details = _xlsx_diff(head_bytes(f), p, aff)
                code = cfgmod.course_for_filename(cfg, f)
                if code:
                    aff["courses"].add(code)
            elif os.path.exists(p):
                old = head_bytes(f)
                new = open(p, "rb").read()
                if f.endswith(".json"):
                    _json_affected(f, old, new, aff)
                elif f.endswith(".csv"):
                    _csv_affected(old, new, aff)
                if old is None:
                    details = ["New file."]
                else:
                    ol, nl = old.decode("utf-8", "ignore").splitlines(), new.decode("utf-8", "ignore").splitlines()
                    import difflib
                    d = [x for x in difflib.unified_diff(ol, nl, lineterm="", n=0)
                         if x[:1] in "+-" and not x.startswith(("+++", "---"))]
                    details = [f"{sum(1 for x in d if x.startswith('+'))} line(s) added, "
                               f"{sum(1 for x in d if x.startswith('-'))} removed."]
            else:
                details = ["Deleted."]
        except Exception as e:  # noqa: BLE001
            details = [f"Could not compute the diff: {e}"]
        label = {"M": "modified", "??": "new (untracked)", "A": "added", "D": "deleted", "?": "changed",
                 "R": "renamed"}.get(st, st)
        out.append({"file": f, "status": label, "details": details,
                    "courses": sorted(aff["courses"]), "weeks": _sort_weeks(aff["weeks"]),
                    "sections": sorted(aff["sections"], key=lambda x: (len(x), x)),
                    "items": sorted(aff["items"]), "records": aff["records"]})
    return out


def all_local_changes() -> list[tuple[str, str]]:
    """Complete `git status` of the clone (tracked changes and untracked files)."""
    return pending_changes(None)


# ----------------------------------------------------------------------------- publish
def github_put_file(token: str, repo: str, repo_path: str, content: bytes, message: str,
                    branch: str = "main", timeout: int = 20) -> None:
    """Commit one file through the GitHub Contents API (create or update)."""
    import requests
    url = f"https://api.github.com/repos/{repo}/contents/{repo_path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers, params={"ref": branch}, timeout=timeout)
    if r.status_code == 200:
        sha = r.json().get("sha")
    elif r.status_code == 404:
        sha = None
    else:
        raise RuntimeError(f"GitHub read error {r.status_code}: {r.text[:200]}")
    payload = {"message": message, "content": base64.b64encode(content).decode("ascii"), "branch": branch}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub write error {r.status_code}: {r.text[:200]}")


def publish(paths, message: str, github: dict | None = None) -> dict:
    """Commit and push `paths`. github = {"token", "repo", "branch"} for API mode (Streamlit Cloud).
    Returns {"ok", "mode", "log": [English lines]}."""
    paths = [p for p in dict.fromkeys(paths) if os.path.exists(path(p))]
    log = []
    locked = [p for p in paths if p.endswith(".xlsx") and excel_locked(p)]
    if locked:
        return {"ok": False, "mode": "none",
                "log": [f"These workbooks are open in Excel: {', '.join(locked)}. Save and close them, then "
                        f"publish again."]}
    if not paths:
        return {"ok": True, "mode": "none", "log": ["Nothing to publish."]}

    if repo_available():
        branch = current_branch()
        for d in (".git/rebase-merge", ".git/rebase-apply"):
            if os.path.exists(path(d)):
                git(["rebase", "--abort"])
                log.append("An unfinished rebase was aborted.")
        r = git(["add", "--"] + paths)
        if r.returncode:
            return {"ok": False, "mode": "git", "log": log + [f"git add failed: {r.stderr.strip()}"]}
        if git(["diff", "--cached", "--quiet"]).returncode == 0:
            log.append("No changes to commit (the files are already up to date).")
        else:
            r = git(["commit", "-m", message])
            if r.returncode:
                return {"ok": False, "mode": "git", "log": log + [f"git commit failed: {r.stderr.strip() or r.stdout.strip()}"]}
            log.append(f"Committed: {message}")
        if git(["remote"]).stdout.strip():
            r = git(["pull", "--rebase", "--autostash", "origin", branch])
            if r.returncode:
                git(["rebase", "--abort"])
                return {"ok": False, "mode": "git",
                        "log": log + ["Syncing with GitHub failed (the remote has conflicting changes). Your commit "
                                      "is kept locally. Details: " + (r.stderr.strip() or r.stdout.strip())[:400]]}
            r = git(["push", "origin", branch])
            if r.returncode:
                return {"ok": False, "mode": "git", "log": log + [f"git push failed: {r.stderr.strip()[:400]}"]}
            log.append(f"Pushed to origin/{branch}. The portal refreshes in about a minute.")
        else:
            log.append("No git remote is configured; the commit is local only.")
        return {"ok": True, "mode": "git", "log": log}

    if github and github.get("token") and github.get("repo"):
        branch = github.get("branch") or "main"
        try:
            for p in paths:
                with open(path(p), "rb") as f:
                    github_put_file(github["token"], github["repo"], p.replace("\\", "/"), f.read(),
                                    message, branch=branch)
                log.append(f"Committed {p} to {github['repo']}@{branch}.")
            return {"ok": True, "mode": "github_api", "log": log}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "mode": "github_api", "log": log + [f"GitHub error: {e}"]}

    return {"ok": True, "mode": "none",
            "log": ["Saved locally only: this folder is not a git clone and GitHub secrets are not configured."]}
