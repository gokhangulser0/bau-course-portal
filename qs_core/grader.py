"""Claude grading pipeline with a review queue.

    1. run_grading()   reads attendance sheets and quiz papers with Claude, tracks tokens/cost live,
                       flags problem papers and saves everything to review_queue/<queue_id>.json.
                       Nothing is written to Excel at this stage.
    2. (instructor)    inspects flagged papers in the dashboard, fixes IDs / scores / feedback,
                       resolves flags and approves the queue.
    3. apply_queue()   writes the approved results to <COURSE>_attendance.xlsx, <COURSE>_grades.xlsx
                       and grades_database.xlsx (stamped backups first, template formatting kept).
"""
from __future__ import annotations

import base64
import difflib
import io
import json
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from . import backup as bk
from . import billing
from . import config as cfgmod
from . import badges
from . import dbio
from . import term_archive
from . import excel_io as xio
from .common import DB_FILE, REVIEW_DIR, clean_id, clean_section, ensure_writable, now_iso, path, read_json, stamp, \
    to_number, touch_meta, write_json

ABSENT_FB = "Absent - 0 points."      # same note as qs_core.absent_zeros.ABSENT_NOTE
MISSING_SIGNATURE_FB = "Your attendance signature is missing, so your quiz score is recorded as 0."
NO_PAPER_FB = "You were present in class, but no quiz submission was found."
TARGETS = {"quiz": "Quiz", "experiment": "Experiment", "homework": "Homework", "midterm": "Midterm",
           "final": "Final", "project": "Project"}


def target_label(target: str) -> str:
    return TARGETS.get(target, str(target).replace("_", " ").title())


def what(target: str) -> str:
    """Human name of the graded paper type."""
    return {"quiz": "quiz", "experiment": "experiment handout", "homework": "homework",
            "project": "project report"}.get(target, f"{target_label(target).lower()} paper")


def sections_of(section) -> list[str]:
    """'901' -> ['901']; '901,902' -> ['901', '902'] (a session may combine sections)."""
    return [x.strip() for x in str(section).replace(";", ",").split(",") if x.strip()]


def target_component(course: dict, target: str) -> dict | None:
    if target == "quiz":
        return cfgmod.quiz_component(course)
    return next((c for c in course["components"] if c["key"] == target), None)


def target_column(comp: dict, number: int | None) -> str:
    """Workbook column that receives the score: 'Exp #3', 'HW #2', or the single column ('Midterm')."""
    if comp["type"] == "items":
        return f"{comp['column_prefix']}{int(number or 1)}"
    return comp.get("column") or comp["label"]


def target_has_feedback(course: dict, target: str) -> bool:
    """Written feedback exists only for Quiz and Experiment / Lab Report (never Homework, Midterm, Final)."""
    return target == "quiz" or cfgmod.has_feedback(target_component(course, target))


def target_absent_zero(course: dict, target: str) -> bool:
    """Absent students without a score receive 0 (Quiz and Experiment / Lab Report only)."""
    return target == "quiz" or cfgmod.absent_zero(target_component(course, target))


def missing_signature_fb(target: str) -> str:
    return f"Your attendance signature is missing, so your {what(target)} score is recorded as 0."


def no_paper_fb(target: str) -> str:
    return f"You were present in class, but no {what(target)} submission was found."

MODEL_ISSUES = {
    "unreadable_id": "The student ID on the paper is hard to read.",
    "ambiguous_handwriting": "Parts of the handwriting are ambiguous.",
    "missing_pages": "The paper seems to have missing pages.",
    "incomplete_scan": "The scan is cut off or incomplete.",
    "blank_page": "The page looks blank.",
    "wrong_quiz": "The paper does not seem to match this quiz or experiment.",
}
BLOCKING = ("error", "warning")   # severities that must be resolved before approval


# ----------------------------------------------------------------------------- job / roster
@dataclass
class GradingJob:
    course: str
    section: str
    week: int
    quiz: int | None              # number of the graded item (quiz number or experiment number)
    model: str
    rubric: str = ""
    attendance_files: list = field(default_factory=list)    # [(name, bytes)]
    quiz_files: list = field(default_factory=list)          # [(name, bytes)]
    question_file: tuple | None = None                      # (name, bytes)
    pages_per_paper: int = 1
    target: str = "quiz"          # "quiz" (grades workbook + portal database) or a component key such as
                                  # "experiment", "homework", "midterm", "final", "project" (component workbook)


def load_roster(course: dict) -> dict:
    """{sid: {"name", "section"}} across all sections (each student keeps the registered section)."""
    roster = {}
    for f in cfgmod.course_files(course):
        for r in xio.read_students(f):
            roster.setdefault(r["Student ID"], {"name": r["Full Name"], "section": r["Section"]})
    if os.path.exists(path(DB_FILE)):
        try:
            db = dbio.read_records()
            ros = dbio.read_roster()
            for _, r in ros[ros["course_code"] == course["code"]].iterrows():
                if len(r["student_id"]) >= 5 and r["student_id"] not in roster:
                    roster[r["student_id"]] = {"name": r["full_name"], "section": r["section"]}
            db = db[db["course_code"].astype(str).str.strip() == course["code"]]
            for _, r in db.iterrows():
                sid = clean_id(r["student_id"])
                if len(sid) >= 5 and sid not in roster:
                    roster[sid] = {"name": str(r["full_name"]).strip(), "section": clean_section(r["section"])}
        except Exception:
            pass
    return roster


def match_to_roster(raw_id, roster_ids) -> tuple[str, str]:
    """(matched_id, how) with how in exact / corrected / ambiguous / unknown / missing."""
    sid = clean_id(raw_id)
    if not sid:
        return "", "missing"
    if not roster_ids or sid in roster_ids:
        return sid, "exact"
    close = difflib.get_close_matches(sid, list(roster_ids), n=2, cutoff=0.8)
    if len(close) == 1:
        return close[0], "corrected"
    if len(close) > 1:
        return sid, "ambiguous"
    return sid, "unknown"


# ----------------------------------------------------------------------------- Claude client
# models that rejected 'temperature' during this desktop session (asked only once per model)
_NO_TEMPERATURE: set = set()


class ClaudeClient:
    def __init__(self, api_key: str, model: str, tracker: billing.UsageTracker):
        from anthropic import Anthropic
        # several papers run in parallel: let the SDK wait and retry on rate limits (429) and overload (529)
        self.client = Anthropic(api_key=api_key, max_retries=6)
        self.model = model
        self.tracker = tracker
        self.notes = []

    def create(self, **kwargs):
        if self.model not in _NO_TEMPERATURE:
            try:
                resp = self.client.messages.create(model=self.model, temperature=0, **kwargs)
                self.tracker.add(resp)
                return resp
            except Exception as e:  # noqa: BLE001
                if "temperature" not in str(e).lower():
                    raise
                _NO_TEMPERATURE.add(self.model)
                note = ("This model does not accept 'temperature'; continuing without it "
                        "(results may vary slightly between runs).")
                if note not in self.notes:
                    self.notes.append(note)
        resp = self.client.messages.create(model=self.model, **kwargs)
        self.tracker.add(resp)
        return resp


def _tool_input(resp, name: str):
    """Arguments of the forced tool call, or None."""
    for b in getattr(resp, "content", None) or []:
        if getattr(b, "type", "") == "tool_use" and getattr(b, "name", "") == name:
            return dict(b.input or {})
    return None


ISSUE_CODES = ["unreadable_id", "ambiguous_handwriting", "missing_pages", "incomplete_scan", "blank_page",
               "wrong_quiz"]


def grade_tool(fb_ok: bool = True) -> dict:
    """Forced tool: the model can only answer with these fields (no preamble, no thinking out loud)."""
    props = {
        "student_id": {"type": "string", "description": "Digits only: the matched valid ID, or the digits as read."},
        "id_confidence": {"type": "number", "description": "0.0-1.0"},
        "quiz_score": {"type": "number", "description": "0-100; equals the sum of breakdown.awarded."},
        "score_confidence": {"type": "number", "description": "0.0-1.0"},
        "breakdown": {"type": "array", "description": "One entry per rubric PART (A, B, C, ...), not per sub-item.",
                      "items": {"type": "object",
                                "properties": {"part": {"type": "string"}, "max": {"type": "number"},
                                               "awarded": {"type": "number"}},
                                "required": ["part", "max", "awarded"]}},
        "issues": {"type": "array", "items": {"type": "string", "enum": ISSUE_CODES}},
    }
    if fb_ok:
        props["feedback"] = {"type": "string",
                             "description": "2-3 short, plain sentences to the student ('You ...'); name deductions."}
    return {"name": "submit_grade", "description": "Submit the grade for this paper. Call it exactly once.",
            "input_schema": {"type": "object", "properties": props, "required": list(props)}}


ATTENDANCE_TOOL = {
    "name": "submit_attendance", "description": "Submit the signatures found on this attendance page.",
    "input_schema": {"type": "object", "properties": {
        "present_ids": {"type": "array", "items": {"type": "string"},
                        "description": "IDs of signed rows and clearly written added IDs."},
        "uncertain_ids": {"type": "array", "items": {"type": "string"},
                          "description": "Handwritten IDs you could not read with certainty, as read."}},
        "required": ["present_ids", "uncertain_ids"]}}


def grade_call(client: "ClaudeClient", system: str, tool: dict, b64_pdf: str, text: str,
               max_tokens: int = 1024) -> dict:
    """One paper -> result dict. The system prompt (rubric + roster) is cached across papers."""
    resp = client.create(
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64_pdf}},
            {"type": "text", "text": text}]}])
    res = _tool_input(resp, tool["name"])
    if res is None:
        res = _parse_json(_text(resp))
    if not isinstance(res, dict):
        raise ValueError("The model did not return a grade object.")
    if getattr(resp, "stop_reason", "") == "max_tokens":
        raise ValueError("The answer was cut off (max_tokens). Re-grade this paper.")
    return res


_OUTPUT_HINT = re.compile(r"(output\s+format|json\s+template|strict\s+output|output\s+only)", re.I)


def strip_output_format(rubric: str) -> str:
    """Remove the rubric's own output-format instructions (they make the model write a long text report
    before the answer). Scoring rules, solution and point distribution are kept."""
    lines, out, skipping = rubric.splitlines(), [], False
    for ln in lines:
        bare = ln.strip()
        if _OUTPUT_HINT.search(bare) and not re.search(r"\d+\s*(pts|points)", bare, re.I):
            skipping = True
            continue
        if skipping:
            if not bare or ln[:1].isspace():
                continue                      # the indented template under the heading
            skipping = False
        out.append(ln)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _text(resp) -> str:
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


def _parse_json(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        pass
    for opener in ("{", "["):
        i = raw.find(opener)
        if i != -1:
            try:
                obj, _ = json.JSONDecoder().raw_decode(raw[i:])
                return obj
            except Exception:
                pass
    raise ValueError(f"The model output could not be parsed as JSON: {raw[:120]}...")


# ----------------------------------------------------------------------------- PDF helpers
def page_images(pdf_bytes: bytes):
    """[(page_no, media_type, base64, (w, h))] using the main embedded image of each page."""
    from pypdf import PdfReader
    out = []
    reader = PdfReader(io.BytesIO(pdf_bytes))
    for i, pg in enumerate(reader.pages, start=1):
        imgs = list(pg.images)
        if not imgs:
            out.append((i, None, None, None))
            continue
        im = imgs[0]
        data, name = im.data, im.name.lower()
        if name.endswith((".jpg", ".jpeg")):
            mt = "image/jpeg"
        elif name.endswith(".png"):
            mt = "image/png"
        else:                               # TIFF and others: convert to JPEG for the API
            from PIL import Image
            pil = im.image.convert("RGB")
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=90)
            data, mt = buf.getvalue(), "image/jpeg"
        size = im.image.size if im.image else None
        out.append((i, mt, base64.b64encode(data).decode(), size))
    return out


def split_pdf(pdf_bytes: bytes, pages_per_paper: int = 1):
    """[(first_page, last_page, base64_pdf)] one entry per student paper."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = len(reader.pages)
    out = []
    for start in range(0, n, pages_per_paper):
        w = PdfWriter()
        for j in range(start, min(start + pages_per_paper, n)):
            w.add_page(reader.pages[j])
        buf = io.BytesIO()
        w.write(buf)
        out.append((start + 1, min(start + pages_per_paper, n), base64.b64encode(buf.getvalue()).decode()))
    return out


def save_preview(pdf_bytes: bytes, page_no: int, dest: str) -> str | None:
    try:
        from pypdf import PdfReader
        pg = PdfReader(io.BytesIO(pdf_bytes)).pages[page_no - 1]
        imgs = list(pg.images)
        if not imgs or imgs[0].image is None:
            return None
        pil = imgs[0].image.convert("RGB")
        pil.thumbnail((1400, 1400))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        pil.save(dest, "JPEG", quality=82)
        return dest
    except Exception:
        return None


# ----------------------------------------------------------------------------- prompts
ATTENDANCE_PROMPT = """You are analyzing a high-resolution scanned course attendance sheet page.
TASK:
1. For printed rows: look closely at the 'Signature' box of each student. If it is signed/marked, the student is PRESENT.
2. For handwritten additions at the bottom or in the margins (students from other sections write their ID there): extract the numerical Student ID.

OUTPUT ONLY a JSON object:
{"present_ids": ["2202739", "2366349"], "uncertain_ids": ["22O2739"], "notes": "optional short note"}"""


def quiz_system_prompt(course: str, section: str, quiz: int, rubric: str, roster_ids, target: str = "quiz",
                       fb_ok: bool = True) -> str:
    ids = ", ".join(sorted(roster_ids)) if roster_ids else "N/A"
    rubric = strip_output_format(rubric)
    feedback = ("""
FEEDBACK:
- Write directly to the student in second person ("You...", "Your...") as their course instructor.
- Never use third-person phrasing such as "The student...".
- 2 to 3 short sentences in simple, plain English. Mention any deductions explicitly.
""" if fb_ok else "")
    if target == "quiz":
        item = f"Quiz #{quiz}"
    elif target == "experiment":
        item = f"Experiment #{quiz} (a lab experiment handout / worksheet filled in by the student)"
    else:
        item = f"{target_label(target)}" + (f" #{quiz}" if quiz else "") + f" ({what(target)})"
    return f"""You are an academic professor grading {course}, Section {section}, {item}.

RUBRIC & OFFICIAL SOLUTION:
{rubric}

Use the rubric ONLY for the solution, point distribution and grading policy (error carried forward,
tolerances, sign/axis conventions, unit slips). Ignore any output format it describes.

STUDENT ID:
- Read the handwritten 'Student ID (Öğrenci No):' in the paper header carefully.
- Valid Student IDs enrolled in this course (ALL sections; students sometimes attend another section): [{ids}]
- If the handwritten digits clearly correspond to exactly one valid ID (at most one uncertain digit), return that ID.
- Otherwise return the digits exactly as you read them. Do NOT force a match to a different student.
- Report how sure you are about the ID in "id_confidence" (0.0-1.0).

SCORING ACCURACY & CONSISTENCY:
- Base the final score on 100 points total.
- The final "quiz_score" MUST equal 100 minus the exact sum of the deductions stated in your feedback.
- "breakdown" has ONE entry per rubric part (A, B, C, ...) with that part's maximum and awarded points
  (do not list sub-items); the awarded points MUST add up to "quiz_score".
- Be fair and generous with partial credit for correct methodology, equations and problem-solving steps.
- Apply error carried forward strictly: never penalize the same mistake twice.
- Report how sure you are about the score in "score_confidence" (0.0-1.0). Lower it when handwriting is ambiguous.
{feedback}
ISSUES: list any of these codes that apply, otherwise an empty list:
unreadable_id, ambiguous_handwriting, missing_pages, incomplete_scan, blank_page, wrong_quiz
(use wrong_quiz when the paper does not match this quiz / experiment)

OUTPUT: grade silently and call the submit_grade tool exactly once. Do not write any other text."""


def generate_rubric(client: ClaudeClient, course: str, name: str, data: bytes) -> str:
    mt = "application/pdf" if name.lower().endswith(".pdf") else ("image/png" if name.lower().endswith(".png")
                                                                  else "image/jpeg")
    block = {"type": "document" if mt == "application/pdf" else "image",
             "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode()}}
    prompt = (f"You are an engineering professor for {course}. The attached document is the official quiz "
              f"question sheet.\nTASK:\n1. Transcribe the problem statement and given values accurately.\n"
              f"2. Provide a complete, step-by-step model solution.\n3. Generate a fair, objective grading "
              f"rubric totaling exactly 100 points, split into lettered parts (A, B, C, ...).")
    resp = client.create(max_tokens=1500, messages=[{"role": "user", "content": [block, {"type": "text",
                                                                                         "text": prompt}]}])
    return _text(resp)


# ----------------------------------------------------------------------------- flags
def compute_flags(p: dict, roster: dict, section: str, low_conf: float, dup_ids: set,
                  present_ids: set | None) -> list[dict]:
    flags = []

    def add(code, sev, msg):
        flags.append({"code": code, "severity": sev, "message": msg})

    if p.get("error"):
        add("grading_error", "error", f"Grading failed: {p['error']}")
        return flags
    sid = p.get("student_id") or ""
    how = p.get("id_match") or ""
    if not sid:
        add("missing_id", "error", "No student ID could be read from the paper. Assign the correct ID.")
    elif sid not in roster:
        add("unknown_id", "error", f"ID {sid} is not on the course roster"
            + (" (it looks like more than one student)." if how == "ambiguous" else "."))
    else:
        home = roster[sid]["section"]
        if home and home not in sections_of(section):
            add("section_mismatch", "info", f"Registered in Section {home}; attended Section {section}. "
                                            f"The record is written to Section {home}.")
        if how == "corrected":
            add("id_corrected", "warning", f"The paper reads '{p.get('raw_id')}'; it was matched to the "
                                           f"closest roster ID {sid}. Confirm it.")
    if sid and sid in dup_ids:
        add("duplicate_id", "error", f"More than one paper has ID {sid}. Fix the ID on the wrong paper or "
                                     f"exclude a duplicate scan.")
    try:
        if p.get("id_confidence") is not None and float(p["id_confidence"]) < low_conf:
            add("low_id_confidence", "warning", f"The model is unsure about the ID "
                                                f"(confidence {float(p['id_confidence']):.2f}).")
        if p.get("score_confidence") is not None and float(p["score_confidence"]) < low_conf:
            add("low_confidence", "warning", f"Low grading confidence ({float(p['score_confidence']):.2f}). "
                                             f"Check the score and feedback.")
    except (TypeError, ValueError):
        pass
    score = p.get("score")
    try:
        score_f = float(score)
        if not 0 <= score_f <= 100:
            add("score_range", "error", f"Score {score} is outside 0-100.")
        bd = p.get("breakdown") or []
        if bd:
            total = sum(float(x.get("awarded") or 0) for x in bd)
            mx = sum(float(x.get("max") or 0) for x in bd)
            if abs(total - score_f) > 0.5:
                add("rubric_mismatch", "error", f"The rubric parts add up to {total:g}, but the score is "
                                                f"{score_f:g}.")
            if mx and abs(mx - 100) > 0.5:
                add("rubric_total", "warning", f"The rubric parts are out of {mx:g} instead of 100.")
    except (TypeError, ValueError):
        add("score_missing", "error", "No numeric score was returned.")
    for code in p.get("issues") or []:
        if code in MODEL_ISSUES:
            add(f"model_{code}", "warning", MODEL_ISSUES[code])
    if p.get("incomplete"):
        add("missing_pages", "warning", "This paper has fewer pages than expected (incomplete scan).")
    if present_ids is not None and sid and sid in roster and sid not in present_ids:
        add("no_signature", "warning", "A paper was found but there is no attendance signature. By policy the "
                                       "score is recorded as 0 unless you add the student to the present list.")
    return flags


def refresh_flags(queue: dict, roster: dict | None = None, low_conf: float | None = None) -> dict:
    """Recompute flags after edits. Keeps 'resolved' only for flags whose message did not change."""
    if roster is None:
        roster = load_roster(cfgmod.course(cfgmod.load(), queue["course"]))
    low_conf = low_conf if low_conf is not None else billing.load_ledger()["settings"]["low_confidence"]
    active = [p for p in queue["papers"] if not p.get("excluded")]
    ids = [p.get("student_id") for p in active if p.get("student_id")]
    dups = {i for i in ids if ids.count(i) > 1}
    present = set(queue["attendance"]["present"]) if queue["attendance"].get("processed") else None
    for p in queue["papers"]:
        old = {f["code"]: f for f in p.get("flags", [])}
        new = compute_flags(p, roster, queue["section"], low_conf, dups, present) if not p.get("excluded") else []
        for f in new:
            o = old.get(f["code"])
            f["resolved"] = bool(o and o.get("resolved") and o.get("message") == f["message"])
        p["flags"] = new
        if p.get("student_id") in roster:
            p["name"] = roster[p["student_id"]]["name"]
            p["home_section"] = roster[p["student_id"]]["section"]
    return queue


def open_flags(queue: dict) -> list[tuple[dict, dict]]:
    out = []
    for p in queue["papers"]:
        if p.get("excluded"):
            continue
        for f in p.get("flags", []):
            if f["severity"] in BLOCKING and not f.get("resolved"):
                out.append((p, f))
    return out


# ----------------------------------------------------------------------------- queue storage
def queue_path(qid: str) -> str:
    return path(REVIEW_DIR, f"{qid}.json")


def save_queue(queue: dict) -> str:
    queue["updated_at"] = now_iso()
    return write_json(queue_path(queue["id"]), queue, backup=False)


def _mark_interrupted(q: dict) -> dict:
    """A run saved as 'running' that no longer runs in the background was cut off (page closed or the app
    restarted). Show it as stopped so the papers graded so far can still be reviewed."""
    if q and q.get("status") == "running":
        from . import jobs
        if not jobs.is_running(q.get("id", "")):
            q["status"] = "stopped"
            note = "This run was interrupted before it finished. The papers graded so far are kept for review."
            if note not in q.setdefault("notes", []):
                q["notes"].append(note)
    return q


def load_queue(qid: str) -> dict | None:
    return _mark_interrupted(read_json(queue_path(qid), None))


def list_queues() -> list[dict]:
    d = path(REVIEW_DIR)
    if not os.path.isdir(d):
        return []
    out = []
    for name in os.listdir(d):
        if name.endswith(".json"):
            q = read_json(os.path.join(d, name), None)
            if q:
                out.append(_mark_interrupted(q))
    out.sort(key=lambda q: q.get("created_at", ""), reverse=True)
    return out


def queue_label(q: dict) -> str:
    kind = target_label(q.get("target", "quiz"))
    quiz = f"{kind} {q['quiz']}" if q.get("quiz") else "Attendance only"
    return (f"{q['course']} · Sec {q['section']} · Week {q['week']} · {quiz} · {q['status'].replace('_', ' ')} "
            f"· {q['created_at'][:16].replace('T', ' ')}")


# ----------------------------------------------------------------------------- run
class RunCancelled(Exception):
    """The instructor pressed Stop."""


def week_attendance(course: dict, section, week: int) -> dict:
    """Attendance already recorded for this week in the given section(s): {"present", "absent", "students"}."""
    from . import absent_zeros as az
    marks = az.attendance_marks(course, az.prepare_db(dbio.read_records()))
    secs = sections_of(section)
    ids = [sid for sid, r in load_roster(course).items() if r["section"] in secs]
    vals = [marks.get((sid, int(week)), "") for sid in ids]
    return {"present": vals.count("+"), "absent": vals.count("-"), "students": len(ids)}


def file_hash(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


def same_scans(files) -> list[dict]:
    """Earlier runs that used exactly the same scanned PDF(s): [{"file", "run"}]."""
    want = {file_hash(d): n for n, d in files}
    out = []
    for q in list_queues():
        for h, name in (q.get("file_hashes") or {}).items():
            if h in want:
                out.append({"file": want[h], "run": queue_label(q)})
    return out


def run_grading(job: GradingJob, api_key: str, on_event=None, cancel=None) -> dict:
    """Grade everything and return a saved review queue (status 'pending_review' or 'stopped')."""
    cfg = cfgmod.load()
    course = cfgmod.course(cfg, job.course)
    if course is None:
        raise ValueError(f"Unknown course {job.course}. Define it in Course Setup first.")
    fb_ok = target_has_feedback(course, job.target)
    led = billing.load_ledger()
    settings = led["settings"]
    tracker = billing.UsageTracker(job.model, settings)
    client = ClaudeClient(api_key, job.model, tracker)
    roster = load_roster(course)
    roster_ids = set(roster)
    tag = {"quiz": "q", "experiment": "e"}.get(job.target, job.target[:3])
    qid = (f"{course.get('term') or 'term'}_{job.course}_{'-'.join(sections_of(job.section))}_w{job.week}"
           f"{('_' + tag + str(job.quiz)) if job.quiz else ''}_{stamp()}")
    prev_dir = path(REVIEW_DIR, qid)
    queue = {"id": qid, "created_at": now_iso(), "term": course.get("term"), "course": job.course,
             "section": str(job.section), "week": int(job.week), "quiz": job.quiz, "target": job.target, "model": job.model,
             "status": "running", "usage": {}, "notes": [],
             "file_hashes": {file_hash(d): n for n, d in job.quiz_files},
             "attendance": {"processed": bool(job.attendance_files), "present": [], "unknown": [], "pages": []},
             "papers": []}

    def emit(kind, done, total, msg):
        queue["usage"] = tracker.as_dict()
        if on_event:
            on_event({"type": kind, "done": done, "total": total, "usage": tracker.as_dict(), "message": msg,
                      "queue_id": qid})

    present = set()
    emit("start", 0, 0, f"Run {qid} started.")
    try:
        # ---- attendance
        att_pages = [(n, pg) for n, d in job.attendance_files for pg in page_images(d)]
        for i, (fname, (pno, mt, b64, _size)) in enumerate(att_pages, start=1):
            if not b64:
                queue["attendance"]["pages"].append({"file": fname, "page": pno, "found": 0,
                                                     "error": "No image layer on this page."})
                emit("attendance", i, len(att_pages), f"{fname} page {pno}: no image layer")
                continue
            try:
                if cancel is not None and cancel.is_set():
                    raise RunCancelled()
                resp = client.create(
                    max_tokens=1000, tools=[ATTENDANCE_TOOL],
                    tool_choice={"type": "tool", "name": ATTENDANCE_TOOL["name"]},
                    messages=[{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}},
                        {"type": "text", "text": ATTENDANCE_PROMPT}]}])
                obj = _tool_input(resp, ATTENDANCE_TOOL["name"])
                if obj is None:
                    obj = _parse_json(_text(resp))
                ids = obj.get("present_ids", obj) if isinstance(obj, dict) else obj
                unsure = obj.get("uncertain_ids", []) if isinstance(obj, dict) else []
                for raw in list(ids) + list(unsure):
                    sid, how = match_to_roster(raw, roster_ids)
                    if sid and sid in roster_ids and raw not in unsure:
                        present.add(sid)
                    elif sid:
                        queue["attendance"]["unknown"].append({"raw": str(raw), "id": sid, "how": how,
                                                               "file": fname, "page": pno, "resolved": False})
                queue["attendance"]["pages"].append({"file": fname, "page": pno, "found": len(ids)})
                emit("attendance", i, len(att_pages), f"{fname} page {pno}: {len(ids)} signature(s)")
            except (billing.BudgetExceeded, RunCancelled):
                raise
            except Exception as e:  # noqa: BLE001
                queue["attendance"]["pages"].append({"file": fname, "page": pno, "found": 0, "error": str(e)})
                emit("attendance", i, len(att_pages), f"{fname} page {pno}: read error ({e})")
            tracker.check_caps()
        queue["attendance"]["present"] = sorted(present)
        if job.attendance_files and not present:
            queue["notes"].append("No signatures were found on the attendance sheets. Check the attendance "
                                  "PDFs before approving: otherwise every student is recorded as absent.")

        # ---- quiz papers
        if job.quiz:
            rubric = (job.rubric or "").strip()
            if not rubric and job.question_file:
                emit("rubric", 0, 1, "Generating a rubric from the question sheet")
                rubric = generate_rubric(client, job.course, *job.question_file)
                queue["generated_rubric"] = rubric
                tracker.check_caps()
            if not rubric:
                raise ValueError("No rubric was given and no question sheet was uploaded.")
            system = quiz_system_prompt(job.course, job.section, job.quiz, rubric, roster_ids, job.target, fb_ok)
            tool = grade_tool(fb_ok)
            queue["rubric"] = rubric                    # kept for targeted re-grades
            queue["pages_per_paper"] = int(job.pages_per_paper)
            papers = [(fname, d, part) for fname, d in job.quiz_files
                      for part in split_pdf(d, max(1, job.pages_per_paper))]
            prepared = []
            for i, (fname, data, (p_from, p_to, b64)) in enumerate(papers, start=1):
                pid = f"p{i:03d}"
                prev = save_preview(data, p_from, os.path.join(prev_dir, f"{pid}.jpg"))
                pdf_path = os.path.join(prev_dir, f"{pid}.pdf")      # this paper's own pages, for re-grades
                try:
                    os.makedirs(prev_dir, exist_ok=True)
                    with open(pdf_path, "wb") as fh:
                        fh.write(base64.b64decode(b64))
                except OSError:
                    pdf_path = None
                paper = {"pid": pid, "file": fname, "page_from": p_from, "page_to": p_to,
                         "preview": os.path.relpath(prev, path()) if prev else None,
                         "paper_pdf": os.path.relpath(pdf_path, path()) if pdf_path else None,
                         "incomplete": (p_to - p_from + 1) < job.pages_per_paper,
                         "excluded": False, "edited": False}
                prepared.append((i, paper, b64))
            _grade_parallel(client, tracker, system, tool, prepared, fb_ok, roster_ids, queue, emit,
                            int(settings.get("parallel_papers") or 4), cancel)
        queue["status"] = "pending_review"
    except (billing.BudgetExceeded, RunCancelled) as e:
        queue["status"] = "stopped"
        queue["notes"].append(str(e) if isinstance(e, billing.BudgetExceeded)
                              else "Stopped by the instructor. The papers graded so far are kept for review.")
        queue["attendance"]["present"] = sorted(set(queue["attendance"]["present"]) | present)
        queue["papers"].sort(key=lambda p: p["pid"])
    finally:
        queue["notes"].extend(client.notes)
        if queue["status"] in ("pending_review", "stopped"):
            try:
                queue["archived_pdfs"] = term_archive.save_run(
                    job.course, job.week, job.section, job.quiz, attendance=job.attendance_files,
                    quizzes=job.quiz_files if job.quiz else [],
                    question=[job.question_file] if (job.quiz and job.question_file) else [],
                    queue_id=qid, term=course.get("term"), target=job.target)
            except Exception as e:  # noqa: BLE001
                queue["notes"].append(f"The PDFs could not be copied to term_archive/{job.course}/: {e}")
        queue["usage"] = tracker.as_dict()
        refresh_flags(queue, roster, settings["low_confidence"])
        save_queue(queue)
        if tracker.calls:
            led = billing.load_ledger()
            billing.record_run(led, tracker, queue_label(queue))
            billing.save_ledger(led)
    return queue


def _grade_parallel(client, tracker, system, tool, prepared, fb_ok, roster_ids, queue, emit, workers, cancel):
    """Grade several papers at the same time. API calls run in worker threads; results, progress events and
    the budget caps are handled here, one at a time, so the review queue and the live counter stay consistent."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    total = len(prepared)
    text = "Grade this submission and call submit_grade."
    todo = iter(prepared)
    pending, done_n, stop = {}, 0, None
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as ex:
        def fill():
            while stop is None and len(pending) < max(1, workers):
                nxt = next(todo, None)
                if nxt is None:
                    return
                pending[ex.submit(grade_call, client, system, tool, nxt[2], text)] = nxt
        fill()
        while pending:
            finished, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for f in finished:
                i, paper, _b64 = pending.pop(f)
                try:
                    res = f.result()
                    sid, how = match_to_roster(res.get("student_id", ""), roster_ids)
                    paper.update({"raw_id": str(res.get("student_id", "")), "student_id": sid, "id_match": how,
                                  "score": res.get("quiz_score"),
                                  "feedback": res.get("feedback", "") if fb_ok else "",
                                  "id_confidence": res.get("id_confidence"),
                                  "score_confidence": res.get("score_confidence"),
                                  "breakdown": res.get("breakdown") or [], "issues": res.get("issues") or []})
                    msg = f"Paper {i}/{total}: ID {sid or '?'} · score {res.get('quiz_score')}"
                except Exception as e:  # noqa: BLE001
                    paper.update({"raw_id": "", "student_id": "", "id_match": "missing", "score": None,
                                  "feedback": "", "error": str(e)})
                    msg = f"Paper {i}/{total}: grading error ({e})"
                queue["papers"].append(paper)
                done_n += 1
                emit("paper", done_n, total, msg)
            if stop is None:
                try:
                    tracker.check_caps()
                except billing.BudgetExceeded as e:
                    stop = e                  # papers already sent finish; nothing new is sent
                if cancel is not None and cancel.is_set():
                    stop = RunCancelled()
            fill()
    queue["papers"].sort(key=lambda p: p["pid"])
    if stop is not None:
        raise stop


# ----------------------------------------------------------------------------- apply (after approval)
# ----------------------------------------------------------------------------- targeted re-grade
def _paper_pdf_b64(queue: dict, p: dict) -> str | None:
    """The paper's own pages: the per-paper copy from the run, or the pages cut from the term archive."""
    if p.get("paper_pdf") and os.path.exists(path(p["paper_pdf"])):
        with open(path(p["paper_pdf"]), "rb") as fh:
            return base64.b64encode(fh.read()).decode()
    # older runs: rebuild from the merged archive PDF (files were merged in upload order)
    from pypdf import PdfReader, PdfWriter
    for rel in queue.get("archived_pdfs", []):
        name = os.path.basename(rel)
        if "_attendance" in name or "question" in name:
            continue
        idx = term_archive.load_index(queue["course"]).get(name, {})
        sources = idx.get("sources", [])
        if p.get("file") not in sources or not os.path.exists(path(rel)):
            continue
        reader = PdfReader(path(rel))
        # pages of the source files merged before this paper's file (taken from the papers' page ranges)
        offset = sum(max((x["page_to"] for x in queue["papers"] if x.get("file") == src), default=0)
                     for src in sources[:sources.index(p["file"])])
        w = PdfWriter()
        for i in range(offset + p["page_from"] - 1, min(offset + p["page_to"], len(reader.pages))):
            w.add_page(reader.pages[i])
        buf = io.BytesIO()
        w.write(buf)
        return base64.b64encode(buf.getvalue()).decode()
    return None


def regrade(queue: dict, pids: list[str], api_key: str, note: str = "", question: str = "",
            rubric: str | None = None, model: str | None = None, on_event=None) -> dict:
    """Re-run Claude only for the selected papers. The previous result of each paper is kept in its
    history; new scores/feedback go back to the review queue and must be approved again."""
    cfg = cfgmod.load()
    course = cfgmod.course(cfg, queue["course"])
    fb_ok = target_has_feedback(course, queue.get("target", "quiz"))
    led = billing.load_ledger()
    model = model or queue.get("model") or led["settings"]["model"]
    tracker = billing.UsageTracker(model, led["settings"])
    client = ClaudeClient(api_key, model, tracker)
    roster = load_roster(course)
    rubric = (rubric if rubric is not None else queue.get("rubric") or queue.get("generated_rubric") or "").strip()
    if not rubric:
        raise ValueError("This run has no stored rubric. Paste the rubric before re-grading.")
    target = queue.get("target", "quiz")
    system = quiz_system_prompt(queue["course"], queue["section"], queue.get("quiz") or 1, rubric, set(roster),
                                target, fb_ok)
    tool = grade_tool(fb_ok)
    extra = ["This paper is being RE-GRADED at the instructor's request. Grade it carefully and independently."]
    if question.strip():
        extra.append(f"Focus on: {question.strip()}. Re-check that part in detail; keep the points of the other "
                     f"parts unless they are clearly wrong.")
    if note.strip():
        extra.append(f"Instructor note: {note.strip()}")
    extra.append("Call submit_grade with the result.")
    by = {p["pid"]: p for p in queue["papers"]}
    done, errors = [], []
    for i, pid in enumerate(pids, start=1):
        p = by[pid]
        b64 = _paper_pdf_b64(queue, p)
        if not b64:
            errors.append(f"{pid}: the original pages could not be found (no per-paper copy and no archive PDF).")
            continue
        old = {k: p.get(k) for k in ("student_id", "score", "feedback", "breakdown", "score_confidence",
                                     "id_confidence", "issues")}
        try:
            res = grade_call(client, system, tool, b64, "\n".join(extra))
            sid, how = match_to_roster(res.get("student_id", ""), set(roster))
            if p.get("id_match") == "manual" and p.get("student_id"):
                sid, how = p["student_id"], "manual"          # keep an ID the instructor assigned by hand
            p.setdefault("history", []).append({"at": now_iso(), "by": "re-grade", **old})
            p.update({"student_id": sid, "id_match": how, "score": res.get("quiz_score"),
                      "feedback": res.get("feedback", "") if fb_ok else "",
                      "id_confidence": res.get("id_confidence"),
                      "score_confidence": res.get("score_confidence"), "breakdown": res.get("breakdown") or [],
                      "issues": res.get("issues") or [], "regraded": True, "regrade_note": note.strip(),
                      "regrade_focus": question.strip()})
            p.pop("error", None)
            done.append(pid)
            msg = f"Re-graded {pid}: score {old.get('score')} -> {res.get('quiz_score')}"
        except billing.BudgetExceeded as e:
            errors.append(str(e))
            break
        except Exception as e:  # noqa: BLE001
            errors.append(f"{pid}: {e}")
            msg = f"{pid}: re-grade error ({e})"
        if on_event:
            on_event({"type": "regrade", "done": i, "total": len(pids), "usage": tracker.as_dict(), "message": msg})
        try:
            tracker.check_caps()
        except billing.BudgetExceeded as e:
            errors.append(str(e))
            break
    u = queue.setdefault("usage", {})
    for k, v in tracker.as_dict().items():
        if k == "model":
            continue
        u[k] = round(u.get(k, 0) + v, 5) if isinstance(v, float) else u.get(k, 0) + v
    queue.setdefault("regrades", []).append({"at": now_iso(), "papers": done, "note": note.strip(),
                                             "focus": question.strip(), "usage": tracker.as_dict(),
                                             "errors": errors})
    if queue["status"] == "applied" and done:
        queue["status"] = "pending_review"
        queue.setdefault("notes", []).append("Papers were re-graded after the results had been written. Approve "
                                             "again to write the new scores and feedback.")
    refresh_flags(queue, roster, led["settings"]["low_confidence"])
    for p in queue["papers"]:
        if p["pid"] in done:
            for f in p.get("flags", []):
                f["resolved"] = False                      # a re-graded paper must be looked at again
    save_queue(queue)
    if tracker.calls:
        led = billing.load_ledger()
        billing.record_run(led, tracker, f"Re-grade · {queue_label(queue)} · {len(done)} paper(s)")
        billing.save_ledger(led)
    return {"done": done, "errors": errors, "usage": tracker.as_dict()}


def _run_context(queue: dict, course: dict) -> dict:
    """Who is present, graded, absent or unknown in this session (shared by the review preview and apply)."""
    from . import absent_zeros as az
    week = int(queue["week"])
    target = queue.get("target", "quiz")
    processed = bool(queue["attendance"].get("processed"))
    roster = load_roster(course)
    present = set(queue["attendance"]["present"]) if processed else set()
    results = {p["student_id"]: p for p in queue["papers"]
               if not p.get("excluded") and p.get("student_id") in roster}
    secs = sections_of(queue["section"])
    section_ids = {sid for sid, r in roster.items() if r["section"] in secs}
    # attendance already on file for this week (workbook + database), read before anything is written
    marks = az.attendance_marks(course, az.prepare_db(dbio.read_records()))
    if not processed:
        # no attendance sheet in this run (e.g. a second quiz in the same session): use the attendance already
        # recorded for this week, so present students without a paper get 0 and absent students get 0
        present = {sid for sid in section_ids if marks.get((sid, week)) == "+"}
    seen = present | set(results)
    visitors = {sid for sid in seen if sid in roster and sid not in section_ids}

    def is_absent(sid):
        if sid in results or sid in present or sid not in section_ids:
            return False
        if marks.get((sid, week)) == "+":
            return False                      # attended another section's session that week
        return True if processed else marks.get((sid, week)) == "-"

    return {"roster": roster, "present": present, "results": results, "secs": secs, "section_ids": section_ids,
            "visitors": visitors, "active": sorted(section_ids | visitors), "processed": processed,
            "marks": marks, "is_absent": is_absent, "week": week, "target": target,
            "fb_ok": target_has_feedback(course, target), "zero_ok": target_absent_zero(course, target)}


def _existing_scores(file_rel: str, column: str) -> dict:
    """{student_id: float} of the real (non-empty) scores already in a workbook column."""
    if not os.path.exists(path(file_rel)):
        return {}
    t = xio.read_table(file_rel)
    if t.empty or column not in t.columns:
        return {}
    out = {}
    for sid, v in zip(t["Student ID"].map(clean_id), t[column]):
        n = to_number(v)
        if n is not None:
            out[sid] = n
    return out


def absent_preview(queue: dict) -> list[dict]:
    """Absent students who will receive 0 when this run is approved:
    [{"Student ID", "Full Name", "Section", "Item", "Week"}]. Students with a real score keep it."""
    cfg = cfgmod.load()
    course = cfgmod.course(cfg, queue["course"])
    if course is None:
        return []
    ctx = _run_context(queue, course)
    item, file_rel = _target_item(course, queue)
    if not ctx["zero_ok"] or not item:
        return []
    existing = _existing_scores(file_rel, item)
    return [{"Student ID": sid, "Full Name": ctx["roster"][sid]["name"], "Section": ctx["roster"][sid]["section"],
             "Item": item, "Week": f"Week {ctx['week']}"}
            for sid in ctx["active"] if ctx["is_absent"](sid) and not existing.get(sid)]


def _target_item(course: dict, queue: dict) -> tuple[str | None, str | None]:
    """(score column, workbook) that this run writes."""
    target, quiz = queue.get("target", "quiz"), queue.get("quiz")
    if target == "quiz":
        return (f"Quiz #{quiz}", course["workbooks"]["grades"]) if quiz else (None, None)
    comp = target_component(course, target)
    if not comp or comp["type"] not in ("items", "single"):
        return None, None
    return target_column(comp, quiz or 1), comp.get("workbook")


def apply_queue(queue: dict) -> dict:
    """Write an approved queue to the course workbooks and the portal database.
    Absent students without a score receive 0 for a Quiz or an Experiment (a real score is never overwritten).
    Returns a change summary used for the pre-push diff."""
    if open_flags(queue):
        raise ValueError("There are unresolved flags. Resolve every flagged paper before writing.")
    cfg = cfgmod.load()
    course = cfgmod.course(cfg, queue["course"])
    term, code, section = course.get("term"), course["code"], str(queue["section"])
    week, quiz = int(queue["week"]), queue.get("quiz")
    target = queue.get("target", "quiz")
    if target != "quiz" and not quiz:
        quiz = 1                              # single-score components (Midterm, Final) have no item number
    to_write = [DB_FILE]
    if queue["attendance"].get("processed"):
        to_write.append(course["workbooks"]["attendance"])
    if target == "quiz":
        to_write.append(course["workbooks"]["grades"])
    else:
        comp_ = target_component(course, target)
        if comp_ and comp_.get("workbook"):
            to_write.append(comp_["workbook"])
    ensure_writable(*to_write)                 # all files are checked before anything is written
    ctx = _run_context(queue, course)
    roster, present, results = ctx["roster"], ctx["present"], ctx["results"]
    secs, section_ids, visitors, active = ctx["secs"], ctx["section_ids"], ctx["visitors"], ctx["active"]
    is_absent, fb_ok, zero_ok, processed = ctx["is_absent"], ctx["fb_ok"], ctx["zero_ok"], ctx["processed"]
    item_col, item_file = _target_item(course, queue)
    existing = _existing_scores(item_file, item_col) if item_col else {}

    def attended(sid):
        """Section whose session the student attended (their own one when it was part of the session)."""
        home = roster.get(sid, {}).get("section", "")
        return home if home in secs else (secs[0] if secs else section)
    summary = {"files": {}, "db": {"updated": 0, "added": 0}, "students": len(active),
               "visitors": sorted(visitors), "present": len(present & set(active)), "absent_zeros": []}

    def outcome(sid):
        """(score, feedback, kind): kind is 'graded', 'absent' (auto 0) or None (leave as it is)."""
        is_p = sid in present
        if quiz and sid in results:
            if not is_p and processed:
                return 0, missing_signature_fb(target), "graded"
            return results[sid]["score"], results[sid]["feedback"], "graded"
        if quiz and is_p:
            return 0, no_paper_fb(target), "graded"
        if quiz and zero_ok and is_absent(sid) and not existing.get(sid):
            return 0, ABSENT_FB, "absent"
        return None, "", None

    outcomes = {sid: outcome(sid) for sid in active}
    for sid, (s_, _, kind) in outcomes.items():
        if kind == "absent":
            summary["absent_zeros"].append({"Student ID": sid, "Full Name": roster[sid]["name"],
                                            "Section": roster[sid]["section"], "Item": item_col,
                                            "Week": f"Week {week}"})

    # attendance workbook
    att_file = course["workbooks"]["attendance"]
    if processed and os.path.exists(path(att_file)):
        att, soft = {}, set()
        for sid in active:
            if sid in present:
                att[sid] = "+"
            elif sid in section_ids:
                att[sid] = "-"
                soft.add(sid)
        r = xio.write_values(att_file, f"Week #{week}", att, soft_keys=soft, term=term, course=code)
        summary["files"][att_file] = {"cells": r["cells"], "students": len(r["students"])}

    def score_and_feedback():
        """{sid: score}, {sid: feedback}, soft ids; absent students only fill empty cells."""
        sc, fb, soft = {}, {}, set()
        for sid, (s_, f_, kind) in outcomes.items():
            if kind is None:
                continue                      # unknown / excused: leave whatever is there (e.g. a make-up score)
            sc[sid] = s_
            if fb_ok:
                fb[sid] = f_
            if kind == "absent":
                soft.add(sid)
        return sc, fb, soft

    # component workbook (Experiment, Homework, Project, Midterm, Final): score column, plus a feedback column
    # for Experiment only; the student portal reads these workbooks directly
    if target != "quiz":
        comp = target_component(course, target)
        if comp is None or comp["type"] not in ("items", "single"):
            raise ValueError(f"{code} has no {target_label(target)} criterion. Add it in Courses & Criteria first.")
        ex_file = comp["workbook"]
        if not os.path.exists(path(ex_file)):
            raise ValueError(f"{ex_file} does not exist yet. Create it on Courses & Criteria (Workbooks) first.")
        col = target_column(comp, quiz)
        sc, fb, soft = score_and_feedback()
        cols = {col: sc}
        if fb_ok and comp.get("feedback"):
            cols[cfgmod.feedback_column(col)] = fb
        r1 = xio.write_many(ex_file, cols, soft_keys={c: soft for c in cols}, term=term, course=code)
        summary["files"][ex_file] = {"cells": r1["cells"], "students": len(r1["students"])}
        badges.mark_updates(code, columns=[col])          # "New" badge for 7 days on the student portal
        summary["publish"] = [ex_file, badges.UPDATES_FILE]
        if not processed:
            queue["status"] = "applied"
            queue["applied_at"] = now_iso()
            queue["apply_summary"] = summary
            save_queue(queue)
            return summary

    # grades workbook (quiz score + quiz feedback)
    gr_file = course["workbooks"]["grades"]
    if quiz and target == "quiz" and os.path.exists(path(gr_file)):
        sc, fb, soft = score_and_feedback()
        r1 = xio.write_many(gr_file, {f"Quiz #{quiz}": sc, f"Quiz #{quiz} FB": fb},
                            soft_keys={f"Quiz #{quiz}": soft, f"Quiz #{quiz} FB": soft}, term=term, course=code)
        summary["files"][gr_file] = {"cells": r1["cells"], "students": len(r1["students"])}

    # portal database
    dbp = path(DB_FILE)
    cols = ["student_id", "full_name", "course_code", "section", "week", "attendance", "quiz_no",
            "quiz_score", "feedback", "attended_section", "updated_at"]
    stamp_now = now_iso()
    db = dbio.read_records(dbp)
    for c in cols:
        if c not in db.columns:
            db[c] = ""
    for c in ("quiz_score", "feedback", "quiz_no", "attended_section", "attendance", "section", "week",
              "full_name", "updated_at"):
        db[c] = db[c].astype(object)
    db["student_id"] = db["student_id"].map(clean_id)
    db["attended_section"] = db["attended_section"].where(db["attended_section"].notna(), "")
    label = f"Quiz {quiz}" if (quiz and target == "quiz") else "No Quiz"
    attendance_only = target != "quiz" or not quiz
    match_labels = {label, "No Quiz", "nan", ""}
    wk_no = pd.to_numeric(db["week"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    course_m = db["course_code"].astype(str).str.strip() == code
    new_rows = []

    def att_mark(sid):
        """Attendance value for the database row ('' = keep what is there when attendance was not read)."""
        if sid in present:
            return "+"
        if processed:
            return "-"
        m_ = ctx["marks"].get((sid, week), "")
        return m_ or ("+" if sid in results else "")

    for sid in active:
        home = roster[sid]["section"]
        is_p = sid in present
        s, f, kind = outcomes[sid]
        mk = att_mark(sid)
        soft_absent = (sid not in present) and (sid not in results)
        m = (course_m & (db["student_id"] == sid) & (wk_no == week)
             & db["quiz_no"].astype(str).str.strip().isin(match_labels)
             & (db["section"].map(clean_section) == home))
        if attendance_only:
            # experiment sessions / attendance-only weeks: never touch quiz scores, only the attendance mark
            if not processed:
                continue
            any_m = course_m & (db["student_id"] == sid) & (wk_no == week) & (db["section"].map(clean_section) == home)
            if any_m.any():
                if soft_absent and (db.loc[any_m, "attendance"] == "+").any():
                    continue
                db.loc[any_m, "attendance"] = "+" if is_p else "-"
                db.loc[any_m, "attended_section"] = attended(sid) if is_p else ""
                db.loc[any_m, "updated_at"] = stamp_now
                summary["db"]["updated"] += 1
            else:
                new_rows.append({"student_id": sid, "full_name": roster[sid]["name"] or "Student",
                                 "course_code": code, "section": home, "week": f"Week {week}",
                                 "attendance": "+" if is_p else "-", "quiz_no": "No Quiz", "quiz_score": None,
                                 "feedback": None, "attended_section": attended(sid) if is_p else "",
                                 "updated_at": stamp_now})
                summary["db"]["added"] += 1
            continue
        if kind is None and not (processed and sid in section_ids):
            continue                          # nothing known about this student for this week
        if m.any():
            idx = db[m].index[0]
            has_score = str(db.at[idx, "quiz_score"]).strip() not in ("", "nan", "None")
            if soft_absent and (db.at[idx, "attendance"] == "+" or has_score):
                continue                      # attended elsewhere / make-up score: keep it
            if mk:
                db.at[idx, "attendance"] = mk
            db.at[idx, "attended_section"] = attended(sid) if is_p else ""
            if kind is not None:
                db.at[idx, "quiz_no"] = label
                db.at[idx, "quiz_score"] = s
                db.at[idx, "feedback"] = f
            db.at[idx, "updated_at"] = stamp_now
            summary["db"]["updated"] += 1
        else:
            new_rows.append({"student_id": sid, "full_name": roster[sid]["name"] or "Student", "course_code": code,
                             "section": home, "week": f"Week {week}", "attendance": mk,
                             "quiz_no": label if kind is not None else "No Quiz", "quiz_score": s, "feedback": f or None,
                             "attended_section": attended(sid) if is_p else "", "updated_at": stamp_now})
            summary["db"]["added"] += 1
    if new_rows:
        db = pd.concat([db, pd.DataFrame(new_rows, columns=cols)], ignore_index=True)
    dbio.write(dbp, records=db, term=term)          # keeps the Roster sheet; stamped backup first
    touch_meta()
    summary["files"][DB_FILE] = {"cells": None, "students": summary["db"]["updated"] + summary["db"]["added"]}

    queue["status"] = "applied"
    queue["applied_at"] = now_iso()
    queue["apply_summary"] = summary
    save_queue(queue)
    return summary
