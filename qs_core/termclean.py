"""Find and remove everything that belongs to a term entered by mistake (e.g. '2026-2027 spring').

Term spellings are compared loosely: '2026-2027 spring', '2026-2027_spring', '2026-2027Spring' and
'2026-2027spring' are the same term.
"""
from __future__ import annotations

import os
import re

from . import config as cfgmod
from .common import ARCHIVE_DIR, BACKUP_DIR, REVIEW_DIR, path

TERM_RE = re.compile(r"^(\d{4})-(\d{4})(fall|spring|summer)$")


def normalise_term(text: str) -> str:
    """'2026-2027 Spring' -> '2026-2027spring' (spaces, underscores and case removed)."""
    s = re.sub(r"[\s_]+", "", str(text or "")).lower()
    s = re.sub(r"^(\d{4})[-./]?(\d{4})", r"\1-\2", s)
    return s


def valid_term(text: str) -> bool:
    m = TERM_RE.match(normalise_term(text))
    return bool(m) and int(m.group(2)) == int(m.group(1)) + 1


def _same(a: str, b: str) -> bool:
    return normalise_term(a) == normalise_term(b)


def _name_has_term(name: str, term: str) -> bool:
    key = normalise_term(term)
    flat = re.sub(r"[\s_]+", "", name).lower()
    return flat.startswith(key) or f"{key}." in flat


def find(term: str) -> dict:
    """Everything connected to `term`: config entries and files (workspace-relative paths)."""
    cfg = cfgmod.load()
    out = {"terms": [t for t in cfg["terms"] if _same(t, term)],
           "active": _same(cfg.get("active_term", ""), term),
           "courses": [c for c, v in cfg["courses"].items() if _same(v.get("term", ""), term)],
           "files": []}
    for d in (BACKUP_DIR, ARCHIVE_DIR, REVIEW_DIR):
        root = path(d)
        if not os.path.isdir(root):
            continue
        for dp, dirs, names in os.walk(root):
            for n in names:
                rel = os.path.relpath(os.path.join(dp, n), path())
                parts = rel.replace("\\", "/").split("/")
                if any(_name_has_term(p, term) for p in parts[1:]):
                    out["files"].append(rel)
    return out


def remove(term: str, delete_files: bool = True, remove_courses: bool = False, fallback_term: str = "") -> list[str]:
    """Remove the term from the configuration (and its files). Returns an English log."""
    found = find(term)
    cfg = cfgmod.load()
    log = []
    if found["terms"]:
        cfg["terms"] = [t for t in cfg["terms"] if not _same(t, term)]
        log.append(f"Removed term '{', '.join(found['terms'])}' from the term list.")
    if found["active"]:
        cfg["active_term"] = fallback_term or (cfg["terms"][0] if cfg["terms"] else "")
        log.append(f"Active term set to {cfg['active_term'] or '(none)'}.")
    if remove_courses:
        for c in found["courses"]:
            del cfg["courses"][c]
            log.append(f"Removed course {c} (term {term}) from the configuration; its workbooks were kept.")
    elif found["courses"]:
        log.append(f"Kept {len(found['courses'])} course(s) that use this term: {', '.join(found['courses'])}. "
                   f"Change their term in Courses & Criteria.")
    cfgmod.save(cfg)
    if delete_files:
        n = 0
        for rel in found["files"]:
            try:
                os.remove(path(rel))
                n += 1
            except OSError as e:
                log.append(f"Could not delete {rel}: {e}")
        if n:
            log.append(f"Deleted {n} file(s) named after the term.")
    if not log:
        log.append("Nothing found for this term.")
    return log
