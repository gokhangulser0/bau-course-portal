"""End-of-semester .zip archive, named after the term (e.g. archives/2026-2027fall.zip)."""
from __future__ import annotations

import json
import os
import zipfile

from . import backup as bk
from . import config as cfgmod
from .common import (ANNOUNCEMENTS_FILE, ARCHIVE_DIR, CONFIG_FILE, DB_FILE, INPUT_DIR, META_FILE, REVIEW_DIR,
                     RUBRIC_FILE, STATUS_FILE, TEMPLATE_DIR, now_iso, path)

GLOBAL_FILES = [DB_FILE, CONFIG_FILE, ANNOUNCEMENTS_FILE, STATUS_FILE, META_FILE, RUBRIC_FILE]


def files_for_term(term: str, include_inputs: bool = True, include_review: bool = True) -> list[str]:
    """Workspace-relative paths that belong in the term archive."""
    cfg = cfgmod.load()
    files = []
    for code in cfgmod.courses_for_term(cfg, term):
        for f in cfgmod.course_files(cfg["courses"][code]):
            if os.path.exists(path(f)) and f not in files:
                files.append(f)
    for f in GLOBAL_FILES:
        if os.path.exists(path(f)) and f not in files:
            files.append(f)
    from . import term_archive
    for code in cfgmod.courses_for_term(cfg, term):
        d = term_archive.course_dir(code)
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                files.append(os.path.relpath(os.path.join(d, n), path()))
    extra_dirs = [TEMPLATE_DIR]
    if include_inputs:
        extra_dirs.append(INPUT_DIR)
    if include_review:
        extra_dirs.append(REVIEW_DIR)
    for d in extra_dirs:
        root = path(d)
        if not os.path.isdir(root):
            continue
        for dp, _, names in os.walk(root):
            for n in sorted(names):
                rel = os.path.relpath(os.path.join(dp, n), path())
                if d == REVIEW_DIR and not os.path.basename(rel).startswith(f"{term}_") \
                        and not rel.replace("\\", "/").split("/")[1].startswith(f"{term}_"):
                    continue
                files.append(rel)
    return files


def make_archive(term: str, include_inputs: bool = True, include_review: bool = True) -> dict:
    if not term:
        raise ValueError("Select a term to archive.")
    files = files_for_term(term, include_inputs, include_review)
    if not files:
        raise ValueError(f"No files found for term {term}.")
    os.makedirs(path(ARCHIVE_DIR), exist_ok=True)
    dest = path(ARCHIVE_DIR, f"{term}.zip")
    previous = bk.backup_file(dest, term=term, course="ARCHIVE") if os.path.exists(dest) else None
    manifest = {"term": term, "created_at": now_iso(), "file_count": len(files), "files": files}
    tmp = dest + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(path(f), arcname=f.replace("\\", "/"))
        z.writestr("ARCHIVE_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    os.replace(tmp, dest)
    return {"path": dest, "file_count": len(files), "size_bytes": os.path.getsize(dest),
            "files": files, "previous_backup": previous}


def list_archives() -> list[dict]:
    d = path(ARCHIVE_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        if n.endswith(".zip"):
            p = os.path.join(d, n)
            out.append({"name": n, "size_bytes": os.path.getsize(p), "path": p})
    return out
