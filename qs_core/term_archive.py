"""Per-course archive of the PDFs used in each grading run.

    term_archive/<COURSE_CODE>/<course>_w<WW>_<section>_<type>.pdf
    e.g. term_archive/MCH2015/mch2015_w04_901_attendance.pdf
         term_archive/MCH2015/mch2015_w04_901_quiz.pdf
         term_archive/MCH2015/mch2015_w04_901_question.pdf

* Several uploaded files of the same type are merged into one PDF (pages in upload order).
* An image question sheet (PNG/JPG) is converted to PDF.
* A second quiz in the same week and section gets the quiz number appended (..._quiz-q5.pdf) so the
  first quiz's papers are never overwritten. Re-running exactly the same week/section/quiz replaces the
  file after a stamped backup.
* term_archive/<COURSE_CODE>/index.json records what each file contains.
"""
from __future__ import annotations

import io
import os
import re

from . import backup as bk
from .common import now_iso, path, read_json, write_json

ROOT = "term_archive"
TYPES = ("attendance", "quiz", "question", "experiment", "experiment-question", "homework", "homework-question",
         "project", "project-question", "midterm", "midterm-question", "final", "final-question")


def _clean(s) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s).strip()).strip("-").lower()
    return s or "x"


def course_dir(code: str) -> str:
    return path(ROOT, re.sub(r"[^A-Za-z0-9_-]+", "", code.upper()) or "COURSE")


def file_name(code: str, week: int, section: str, kind: str, quiz: int | None = None, suffix_quiz=False,
              tag: str = "q") -> str:
    base = f"{_clean(code)}_w{int(week):02d}_{_clean(section)}_{kind}"
    if suffix_quiz and quiz:
        base += f"-{tag}{int(quiz)}"
    return base + ".pdf"


def _index_path(code: str) -> str:
    return os.path.join(course_dir(code), "index.json")


def load_index(code: str) -> dict:
    return read_json(_index_path(code), {}) or {}


def _to_pdf(files: list[tuple[str, bytes]]) -> bytes | None:
    """Merge PDFs / images into one PDF."""
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for name, data in files:
        if name.lower().endswith(".pdf"):
            for page in PdfReader(io.BytesIO(data)).pages:
                writer.add_page(page)
        else:
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "PDF", resolution=150)
            for page in PdfReader(io.BytesIO(buf.getvalue())).pages:
                writer.add_page(page)
    if not writer.pages:
        return None
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def save_run(code: str, week: int, section: str, quiz: int | None, attendance=None, quizzes=None,
             question=None, queue_id: str = "", term: str | None = None, target: str = "quiz") -> list[str]:
    """Save the run's PDFs. Each argument is a list of (file_name, bytes). Returns workspace-relative paths.
    target "quiz" stores ..._quiz.pdf / ..._question.pdf; any other assessment key stores ..._<key>.pdf and
    ..._<key>-question.pdf (e.g. _experiment.pdf, _midterm.pdf, _homework-question.pdf)."""
    d = course_dir(code)
    os.makedirs(d, exist_ok=True)
    index = load_index(code)
    saved = []
    paper_kind = "quiz" if target == "quiz" else target
    question_kind = "question" if target == "quiz" else f"{target}-question"
    groups = {"attendance": attendance or [], paper_kind: quizzes or [], question_kind: question or []}
    tag = {"quiz": "q", "experiment": "e"}.get(target, "n")
    for kind, files in groups.items():
        if not files:
            continue
        name = file_name(code, week, section, kind)
        prev = index.get(name)
        if kind != "attendance" and quiz and prev and prev.get("quiz") and int(prev["quiz"]) != int(quiz):
            name = file_name(code, week, section, kind, quiz, suffix_quiz=True, tag=tag)
        data = _to_pdf(files)
        if not data:
            continue
        dest = os.path.join(d, name)
        if os.path.exists(dest):
            bk.backup_file(dest, term=term, course=code.upper())
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        index[name] = {"course": code.upper(), "week": int(week), "section": str(section), "type": kind,
                       "quiz": int(quiz) if quiz and kind != "attendance" else None,
                       "sources": [n for n, _ in files], "pages": _page_count(data), "queue_id": queue_id,
                       "archived_at": now_iso()}
        saved.append(os.path.relpath(dest, path()))
    if saved:
        write_json(_index_path(code), index, backup=False)
    return saved


def _page_count(data: bytes) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return 0


def list_course(code: str) -> list[dict]:
    """Archived files of a course, sorted by week, section, type."""
    d = course_dir(code)
    if not os.path.isdir(d):
        return []
    index = load_index(code)
    out = []
    for n in os.listdir(d):
        if not n.lower().endswith(".pdf"):
            continue
        meta = index.get(n, {})
        out.append({"file": n, "path": os.path.join(d, n), "week": meta.get("week"), "section": meta.get("section"),
                    "type": meta.get("type"), "quiz": meta.get("quiz"), "pages": meta.get("pages"),
                    "sources": ", ".join(meta.get("sources", [])), "archived_at": meta.get("archived_at", ""),
                    "size_bytes": os.path.getsize(os.path.join(d, n))})
    out.sort(key=lambda r: (r["week"] or 0, str(r["section"]), TYPES.index(r["type"]) if r["type"] in TYPES else 9,
                            r["file"]))
    return out


def archived_courses() -> list[str]:
    root = path(ROOT)
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))
