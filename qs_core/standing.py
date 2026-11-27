"""Course standing, per-component contributions and What-If projections, driven by courses_config.json.

Points of a multi-item component with weight W:
    best_n     W * sum(best N scores) / N / 100          (missing items count as 0 inside the N)
    all        W * sum(scores) / count / 100              (missing items count as 0)
    evaluated  W * mean(evaluated scores) / 100           (missing items are ignored)
Single components: W * score / 100. Attendance: W * attended / held.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config as cfgmod
from .common import clean_id, to_number


@dataclass
class Item:
    label: str
    score: float | None          # 0-100, None = pending / not graded
    held: bool                   # has the class been graded on this item yet?
    contribution: float = 0.0    # points this item adds right now (for "all"/single rules)


@dataclass
class ComponentResult:
    comp: dict
    items: list = field(default_factory=list)
    earned: float = 0.0
    started: bool = False        # at least one item graded class-wide
    display_avg: float | None = None

    @property
    def key(self):
        return self.comp["key"]

    @property
    def weight(self):
        return self.comp["weight"]

    @property
    def evaluated(self):
        return [i.score for i in self.items if i.score is not None]

    @property
    def pending_count(self):
        return sum(1 for i in self.items if not i.held)


def _count(comp: dict) -> int:
    return comp.get("count", 1) if comp["type"] in ("quiz", "items") else 1


def points(values, comp: dict) -> float:
    """Points earned from a list of 0-100 scores (only items that exist; missing ones are absent)."""
    vals = [float(v) for v in values if v is not None]
    w = comp["weight"]
    if comp["type"] in ("single",):
        return w * vals[0] / 100.0 if vals else 0.0
    if comp["type"] == "attendance":
        return w * vals[0] / 100.0 if vals else 0.0
    rule = comp.get("rule", "all")
    n = _count(comp)
    if rule == "best_n":
        k = comp.get("best_n", n)
        top = sorted(vals, reverse=True)[:k]
        return w * sum(top) / k / 100.0
    if rule == "evaluated":
        return w * (sum(vals) / len(vals)) / 100.0 if vals else 0.0
    return w * sum(vals) / n / 100.0


def display_average(values, comp: dict):
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    if comp["type"] in ("quiz", "items") and comp.get("rule") == "best_n":
        top = sorted(vals, reverse=True)[: comp.get("best_n", len(vals))]
        return sum(top) / len(top)
    return sum(vals) / len(vals)


def rule_text(comp: dict) -> str:
    if comp["type"] in ("quiz", "items"):
        if comp.get("rule") == "best_n":
            return f"best {comp['best_n']} of {comp['count']}"
        if comp.get("rule") == "evaluated":
            return "average of evaluated items"
        return f"all {comp['count']} count"
    if comp["type"] == "attendance":
        return "share of held weeks attended"
    return "single score"


# ----------------------------------------------------------------------------- data gathering
def student_row(table: pd.DataFrame, sid: str) -> dict | None:
    if table is None or table.empty or "Student ID" not in table.columns:
        return None
    m = table[table["Student ID"].map(clean_id) == sid]
    return m.iloc[0].to_dict() if not m.empty else None


def column_held(table: pd.DataFrame, col: str) -> bool:
    """A workbook column counts as 'held' once any student has a numeric score in it."""
    if table is None or table.empty or col not in table.columns:
        return False
    return pd.to_numeric(table[col], errors="coerce").notna().any()


def compute(course: dict, sid: str, quiz_scores: dict, held_quizzes: set, tables: dict,
            attendance: tuple[int, int] | None = None) -> list[ComponentResult]:
    """quiz_scores {quiz_no: score}; held_quizzes {quiz_no}; tables {workbook: DataFrame};
    attendance (attended_weeks, held_weeks)."""
    out = []
    for comp in course["components"]:
        res = ComponentResult(comp)
        t = comp["type"]
        if t == "quiz":
            for q in range(1, comp["count"] + 1):
                held = q in held_quizzes
                res.items.append(Item(f"Quiz {q}", quiz_scores.get(q), held or q in quiz_scores))
            vals = list(quiz_scores.values())
        elif t in ("items", "single"):
            table = tables.get(comp["workbook"])
            row = student_row(table, sid) or {}
            for col in cfgmod.item_columns(comp):
                res.items.append(Item(col, to_number(row.get(col)), column_held(table, col)))
            vals = [i.score for i in res.items if i.score is not None]
        elif t == "attendance":
            att, held = attendance or (0, 0)
            pct = (100.0 * att / held) if held else None
            res.items.append(Item("Attendance", pct, bool(held)))
            vals = [pct] if pct is not None else []
        else:
            continue
        res.started = any(i.held for i in res.items)
        res.earned = points(vals, comp)
        res.display_avg = display_average(vals, comp)
        if t in ("single", "items") and comp.get("rule") != "best_n":
            n = _count(comp)
            for i in res.items:
                if i.score is not None:
                    i.contribution = comp["weight"] * i.score / 100.0 / (1 if t == "single" else n) \
                        if comp.get("rule") != "evaluated" else 0.0
        out.append(res)
    return out


def totals(results: list[ComponentResult]) -> tuple[float, float]:
    """(earned, base) where base = weights of components that have started."""
    earned = sum(r.earned for r in results if r.weight > 0)
    base = sum(r.weight for r in results if r.weight > 0 and r.started)
    return earned, base


def what_if(results: list[ComponentResult], assumed: dict) -> dict:
    """assumed {component_key: average score on the items still to come}.
    Returns {"per": {key: (current, projected, max)}, "current", "projected", "max", "base"}."""
    per, cur_t, proj_t, max_t, base = {}, 0.0, 0.0, 0.0, 0.0
    for r in results:
        if r.weight <= 0:
            continue
        comp = r.comp
        taken = r.evaluated
        pending = r.pending_count
        x = assumed.get(r.key)
        if comp["type"] == "attendance":
            cur = proj = mx = r.earned
        else:
            fut = [float(x)] * pending if x is not None else []
            cur = r.earned
            proj = points(taken + fut, comp) if fut else cur
            mx = points(taken + [100.0] * pending, comp) if pending else cur
        per[r.key] = (cur, proj, mx)
        cur_t += cur
        proj_t += proj
        max_t += mx
        base += r.weight
    return {"per": per, "current": cur_t, "projected": proj_t, "max": max_t, "base": base}
