"""Course configuration (courses_config.json).

Replaces every hard-coded per-course rule. Structure:

{
  "active_term": "2026-2027fall",
  "terms": ["2026-2027fall"],
  "courses": {
    "MCH2008": {
      "code": "MCH2008", "title": "Engineering Mechanics", "term": "2026-2027fall",
      "sections": ["901", "902"], "weeks": 9, "show_standing": true,
      "workbooks": {"attendance": "MCH2008_attendance.xlsx", "grades": "MCH2008_grades.xlsx"},
      "components": [
        {"key": "quiz", "label": "Quiz", "type": "quiz", "weight": 20, "count": 9,
         "rule": "best_n", "best_n": 7},
        {"key": "homework", "label": "Homework", "type": "items", "weight": 20, "count": 5,
         "column_prefix": "HW #", "rule": "all", "workbook": "MCH2008_homework.xlsx"},
        {"key": "midterm", "label": "Midterm", "type": "single", "weight": 20,
         "column": "Midterm", "workbook": "MCH2008_midterm.xlsx"}
      ]
    }
  }
}

Component types
  quiz        scores come from grades_database.xlsx (written by the AI grading pipeline)
  items       several numbered columns in a course workbook (e.g. HW #1 ... HW #5)
  single      one column in a course workbook (e.g. Midterm, Final, Project)
  attendance  percentage of held weeks attended (from grades_database.xlsx)

Aggregation rules for quiz / items
  best_n      only the best N of `count` items count (missing items count as 0 inside the N)
  all         every one of the `count` items counts equally (missing = 0)
  evaluated   average of the items evaluated so far (missing items are ignored)
"""
from __future__ import annotations

import copy
import re

from .common import CONFIG_FILE, read_json, slug, write_json

COMPONENT_TYPES = ["quiz", "items", "single", "attendance"]
RULES = {
    "best_n": "Best N of M",
    "all": "All items count (missing = 0)",
    "evaluated": "Average of evaluated items only",
}
TYPE_LABELS = {
    "quiz": "Quiz (AI-graded, stored in the portal database)",
    "items": "Numbered items in a workbook (e.g. HW #1..#5, Exp #1..#6)",
    "single": "Single score column in a workbook (e.g. Midterm)",
    "attendance": "Attendance percentage",
}

# Default workbook names for common components (user example: MCH2008_midterms.xlsx)
_DEFAULT_WB_SUFFIX = {"midterm": "midterms", "final": "finals", "homework": "homework", "hw": "homework",
                      "project": "project", "experiment": "experiments"}

# The five standard evaluation criteria and how each one is stored by default
CRITERIA = {
    "Project": {"key": "project", "type": "single", "column": "Project", "multi_prefix": "Project #"},
    "Quiz": {"key": "quiz", "type": "quiz"},
    "Homework": {"key": "homework", "type": "items", "column_prefix": "HW #"},
    "Midterm": {"key": "midterm", "type": "single", "column": "Midterm", "multi_prefix": "Midterm #"},
    "Final": {"key": "final", "type": "single", "column": "Final"},
    # Lab experiment handouts / worksheets: one score + one feedback column per experiment
    "Experiment": {"key": "experiment", "type": "items", "column_prefix": "Exp #", "feedback": True},
}
CRITERIA_ORDER = ["Quiz", "Homework", "Experiment", "Project", "Midterm", "Final"]
# Criteria that the Claude grading pipeline can grade from scanned papers
AI_GRADABLE = ("Quiz", "Experiment")
# Written feedback exists only for these criteria (never for Homework, Midterm, Final or Project)
FEEDBACK_CRITERIA = ("Quiz", "Experiment")
# Weekly in-class, attendance-linked criteria: an absent student without a score receives 0
ABSENT_ZERO_CRITERIA = ("Quiz", "Experiment")


def criterion_of(comp: dict) -> str | None:
    """Which standard criterion a component represents (None for custom components)."""
    k = comp.get("key", "")
    for name, spec in CRITERIA.items():
        if k == spec["key"] or (comp.get("label", "").strip().lower() == name.lower()):
            return name
    if comp.get("type") == "quiz":
        return "Quiz"
    return None


def build_criterion(name: str, code: str, weight: float, count: int = 1, rule: str = "all", best_n=None,
                    existing: dict | None = None) -> dict:
    """Component dict for one standard criterion (keeps the workbook/column names of an existing one)."""
    spec = CRITERIA[name]
    comp = dict(existing or {})
    comp.update({"label": name, "key": spec["key"], "weight": float(weight)})
    if name == "Quiz":
        comp.update({"type": "quiz", "count": max(1, int(count)), "rule": rule})
    elif name in ("Homework", "Experiment") or (count and int(count) > 1):
        comp["type"] = "items"
        comp["count"] = max(1, int(count))
        comp["rule"] = rule
        comp.setdefault("column_prefix", spec.get("column_prefix") or spec.get("multi_prefix") or f"{name} #")
        comp.pop("column", None)
    else:
        comp["type"] = "single"
        comp.setdefault("column", spec.get("column") or name)
        for k in ("count", "rule", "best_n", "column_prefix"):
            comp.pop(k, None)
    if comp.get("rule") == "best_n":
        comp["best_n"] = int(best_n or comp.get("best_n") or comp["count"])
    else:
        comp.pop("best_n", None)
    return normalise_component(comp, code)

DEFAULT_CONFIG = {
    "active_term": "2026-2027fall",
    "terms": ["2026-2027fall"],
    "courses": {
        "MCH2008": {
            "code": "MCH2008",
            "title": "Engineering Mechanics",
            "term": "2026-2027fall",
            "sections": ["901", "902"],
            "weeks": 9,
            "show_standing": True,
            "workbooks": {"attendance": "MCH2008_attendance.xlsx", "grades": "MCH2008_grades.xlsx"},
            "components": [
                {"key": "quiz", "label": "Quiz", "type": "quiz", "weight": 20.0, "count": 9,
                 "rule": "best_n", "best_n": 7},
                {"key": "homework", "label": "Homework", "type": "items", "weight": 20.0, "count": 5,
                 "column_prefix": "HW #", "rule": "all", "workbook": "MCH2008_homework.xlsx"},
                {"key": "midterm", "label": "Midterm", "type": "single", "weight": 20.0,
                 "column": "Midterm", "workbook": "MCH2008_midterm.xlsx"},
            ],
        },
        "MCH2015": {
            "code": "MCH2015",
            "title": "Introduction to Heat and Fluid Dynamics",
            "term": "2026-2027fall",
            "sections": ["901", "902"],
            "weeks": 9,
            # Weights for MCH2015 were never defined in the old code: the portal only showed quiz
            # charts. Set the real weights in Course Setup and switch this on.
            "show_standing": False,
            "workbooks": {"attendance": "MCH2015_attendance.xlsx", "grades": "MCH2015_grades.xlsx"},
            "components": [
                {"key": "quiz", "label": "Quiz", "type": "quiz", "weight": 0.0, "count": 5,
                 "rule": "all"},
            ],
        },
    },
    "settings": {
        "newest_first": True,
        "min_class_for_chart": 5,
        "announcement_feed_size": 5,
        "backup_keep_per_file": 40,
    },
}


# ----------------------------------------------------------------------------- load / save
def load() -> dict:
    cfg = read_json(CONFIG_FILE, None)
    if not cfg:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.setdefault("courses", {})
    cfg.setdefault("terms", [])
    cfg.setdefault("settings", {})
    for k, v in DEFAULT_CONFIG["settings"].items():
        cfg["settings"].setdefault(k, v)
    if not cfg.get("active_term"):
        cfg["active_term"] = cfg["terms"][0] if cfg["terms"] else ""
    for code, c in cfg["courses"].items():
        normalise_course(c, code)
        if c.get("term") and c["term"] not in cfg["terms"]:
            cfg["terms"].append(c["term"])
    return cfg


def save(cfg: dict) -> str:
    return write_json(CONFIG_FILE, cfg, backup=True, term=cfg.get("active_term"), course=None)


def ensure_file() -> dict:
    """Create courses_config.json from the defaults if it does not exist yet."""
    cfg = read_json(CONFIG_FILE, None)
    if cfg:
        return load()
    cfg = load()
    write_json(CONFIG_FILE, cfg, backup=False)
    return cfg


# ----------------------------------------------------------------------------- normalisation
def default_workbook(code: str, comp: dict) -> str:
    base = slug(comp.get("key") or comp.get("label"))
    return f"{code}_{_DEFAULT_WB_SUFFIX.get(base, base)}.xlsx"


def normalise_component(comp: dict, code: str) -> dict:
    comp.setdefault("label", comp.get("key", "Component").title())
    comp["key"] = slug(comp.get("key") or comp["label"])
    comp.setdefault("type", "single")
    comp["weight"] = float(comp.get("weight") or 0.0)
    t = comp["type"]
    if t in ("quiz", "items"):
        comp["count"] = max(1, int(comp.get("count") or 1))
        comp.setdefault("rule", "all")
        if comp["rule"] not in RULES:
            comp["rule"] = "all"
        if comp["rule"] == "best_n":
            comp["best_n"] = max(1, min(int(comp.get("best_n") or comp["count"]), comp["count"]))
    if t == "items":
        comp.setdefault("column_prefix", f"{comp['label']} #")
        comp.setdefault("workbook", default_workbook(code, comp))
    if t == "single":
        comp.setdefault("column", comp["label"])
        comp.setdefault("workbook", default_workbook(code, comp))
    if t in ("items", "single"):
        comp["feedback"] = has_feedback(comp)      # Experiment only; never Homework, Midterm, Final
    return comp


def has_feedback(comp: dict | None) -> bool:
    """True when written feedback belongs to this criterion (Quiz and Experiment / Lab Report only)."""
    return bool(comp) and criterion_of(comp) in FEEDBACK_CRITERIA


def absent_zero(comp: dict | None) -> bool:
    """True when an absent student without a score receives 0 for this criterion."""
    return bool(comp) and criterion_of(comp) in ABSENT_ZERO_CRITERIA


def normalise_course(c: dict, code: str) -> dict:
    c["code"] = code
    c.setdefault("title", "")
    c.setdefault("term", "")
    c["sections"] = [str(s).strip() for s in (c.get("sections") or []) if str(s).strip()]
    c["weeks"] = int(c.get("weeks") or 14)
    c.setdefault("show_standing", True)
    # Roster column that groups students into section sheets: "Section" or "Lab Section"
    if c.get("roster_section_column") not in ("Section", "Lab Section"):
        c["roster_section_column"] = "Section"
    wbs = c.setdefault("workbooks", {})
    wbs.setdefault("attendance", f"{code}_attendance.xlsx")
    wbs.setdefault("grades", f"{code}_grades.xlsx")
    c["components"] = [normalise_component(dict(x), code) for x in (c.get("components") or [])]
    return c


# ----------------------------------------------------------------------------- queries
def course(cfg: dict, code: str) -> dict | None:
    return cfg["courses"].get(code)


def courses_for_term(cfg: dict, term: str | None = None) -> list[str]:
    term = term or cfg.get("active_term")
    return sorted(code for code, c in cfg["courses"].items() if not term or c.get("term") == term)


def all_courses(cfg: dict) -> list[str]:
    return sorted(cfg["courses"])


def quiz_component(c: dict) -> dict | None:
    return next((x for x in c["components"] if x["type"] == "quiz"), None)


def feedback_column(score_col: str) -> str:
    return f"{score_col} FB"


def workbook_columns(comp: dict) -> list[str]:
    """Columns of a component workbook: scores, plus a feedback column after each score when enabled."""
    out = []
    for col in item_columns(comp):
        out.append(col)
        if comp.get("feedback"):
            out.append(feedback_column(col))
    return out


def experiment_component(c: dict) -> dict | None:
    return next((x for x in c["components"] if criterion_of(x) == "Experiment"), None)


def item_columns(comp: dict) -> list[str]:
    if comp["type"] == "items":
        return [f"{comp['column_prefix']}{i}" for i in range(1, comp["count"] + 1)]
    if comp["type"] == "single":
        return [comp["column"]]
    return []


def workbook_components(c: dict) -> list[dict]:
    """Components that live in their own course workbook (items / single)."""
    return [x for x in c["components"] if x["type"] in ("items", "single")]


def course_files(c: dict) -> list[str]:
    """Every course-coded workbook that belongs to a course."""
    files = [c["workbooks"]["attendance"], c["workbooks"]["grades"]]
    for comp in workbook_components(c):
        if comp["workbook"] not in files:
            files.append(comp["workbook"])
    return files


def published_course_files(c: dict) -> list[str]:
    """Workbooks the student portal reads directly (pushed to GitHub)."""
    return [comp["workbook"] for comp in workbook_components(c)]


def course_for_filename(cfg: dict, filename: str) -> str | None:
    name = filename.replace("\\", "/").split("/")[-1]
    for code, c in cfg["courses"].items():
        if name in course_files(c) or name.upper().startswith(code.upper() + "_"):
            return code
    m = re.match(r"([A-Za-z]{2,6}\d{3,5})_", name)
    return m.group(1).upper() if m else None


def validate_course(c: dict) -> list[str]:
    """Human-readable warnings about a course definition."""
    msgs = []
    if not re.fullmatch(r"[A-Za-z]{2,6}\d{3,5}", c["code"]):
        msgs.append("The course code should look like 'MCH2008' (letters followed by digits).")
    if not c["sections"]:
        msgs.append("Define at least one section (e.g. 901).")
    total = sum(x["weight"] for x in c["components"])
    if c.get("show_standing") and abs(total - 100.0) > 1e-6:
        msgs.append(f"Component weights add up to {total:g}%, not 100%. Students will see their standing "
                    f"out of the weights of the components that have started.")
    keys = [x["key"] for x in c["components"]]
    if len(keys) != len(set(keys)):
        msgs.append("Two components share the same key. Give each component a unique name.")
    if sum(1 for x in c["components"] if x["type"] == "quiz") > 1:
        msgs.append("Only one quiz component per course is supported (the AI grading pipeline writes to it).")
    for x in c["components"]:
        if x["type"] in ("quiz", "items") and x["rule"] == "best_n" and x["best_n"] > x["count"]:
            msgs.append(f"{x['label']}: best N cannot be larger than the total count.")
    return msgs


def weight_summary(c: dict) -> str:
    parts = []
    for x in c["components"]:
        if x["weight"] <= 0:
            continue
        desc = f"{x['label']} {x['weight']:g}%"
        if x["type"] in ("quiz", "items"):
            if x["rule"] == "best_n":
                desc += f" (best {x['best_n']} of {x['count']})"
            elif x["rule"] == "all":
                desc += f" (all {x['count']})"
            else:
                desc += " (average of evaluated)"
        parts.append(desc)
    return ", ".join(parts)
