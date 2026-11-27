"""Instructor announcements (announcements.json) shown in the student portal feed.

Scopes
  all       every course and section
  course    every section of one course
  sections  selected sections of one course
"""
from __future__ import annotations

import uuid

from .common import ANNOUNCEMENTS_FILE, now_iso, read_json, write_json

SCOPES = {"all": "All courses", "course": "Specific course (all sections)",
          "sections": "Specific course + selected section(s)"}


def load() -> list[dict]:
    data = read_json(ANNOUNCEMENTS_FILE, [])
    items = data.get("announcements", []) if isinstance(data, dict) else data
    for a in items:
        a.setdefault("id", uuid.uuid4().hex[:10])
        a.setdefault("title", "")
        a.setdefault("body", "")
        a.setdefault("scope", "all")
        a.setdefault("course", "")
        a.setdefault("sections", [])
        a.setdefault("active", True)
        a.pop("level", None)              # priority levels were removed; older files may still have one
        a.setdefault("created_at", now_iso())
        a.setdefault("updated_at", a["created_at"])
    return items


def save(items: list[dict], term: str | None = None) -> str:
    return write_json(ANNOUNCEMENTS_FILE, {"announcements": items}, backup=True, term=term, course="GLOBAL")


def create(items: list[dict], title: str, body: str, scope: str, course: str = "", sections=None,
           active: bool = True) -> dict:
    ts = now_iso()
    a = {"id": uuid.uuid4().hex[:10], "title": title.strip(), "body": body.strip(), "scope": scope,
         "course": course if scope != "all" else "", "sections": list(sections or []) if scope == "sections" else [],
         "active": active, "created_at": ts, "updated_at": ts}
    items.append(a)
    return a


def update(items: list[dict], ann_id: str, **fields) -> dict | None:
    for a in items:
        if a["id"] == ann_id:
            a.update(fields)
            if a["scope"] == "all":
                a["course"], a["sections"] = "", []
            elif a["scope"] == "course":
                a["sections"] = []
            a["updated_at"] = now_iso()
            return a
    return None


def delete(items: list[dict], ann_id: str) -> list[dict]:
    return [a for a in items if a["id"] != ann_id]


def scope_label(a: dict) -> str:
    if a["scope"] == "all":
        return "All courses"
    if a["scope"] == "course":
        return f"{a['course']} (all sections)"
    return f"{a['course']} · Section {', '.join(a['sections'])}"


def relevant(items: list[dict], enrolments: list[tuple[str, str]] | None, limit: int = 5) -> list[dict]:
    """Active announcements for a student enrolled in [(course, section), ...] (None = before login:
    only 'all' announcements). Newest first."""
    out = []
    for a in items:
        if not a.get("active"):
            continue
        if a["scope"] == "all":
            out.append(a)
        elif enrolments:
            for course, section in enrolments:
                if a["course"] != course:
                    continue
                if a["scope"] == "course" or (section and section in a["sections"]):
                    out.append(a)
                    break
    out.sort(key=lambda a: a.get("updated_at") or a.get("created_at") or "", reverse=True)
    return out[:limit]
