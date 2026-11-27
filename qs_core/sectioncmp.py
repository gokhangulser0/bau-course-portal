"""Section comparison & deviation summary for one course (average scores and attendance per section).

A spread (best section minus worst section) of at least SCORE_GAP points in an average score, or ATT_GAP
percentage points in attendance, is flagged as noticeable.
"""
from __future__ import annotations

import pandas as pd

from . import config as cfgmod
from .common import clean_section

SCORE_GAP = 10.0
ATT_GAP = 15.0


def _sec_key(s):
    return (0, int(s), "") if str(s).isdigit() else (1, 0, str(s))


def compare(course: dict, db: pd.DataFrame, tables: dict, score_gap: float = SCORE_GAP,
            att_gap: float = ATT_GAP) -> dict:
    """{"sections", "metrics": [{"name", "values": {sec: v}, "unit", "spread", "flag"}], "flagged"}."""
    import portal_logic as pl
    code = course["code"]
    metrics = []
    cdb = db[db["course_code"] == code] if db is not None and not db.empty else pd.DataFrame()
    sections = set(course.get("sections") or [])
    if not cdb.empty:
        sections |= set(cdb["section"].dropna())
        scored = cdb[cdb["quiz_score_num"].notna()]
        if not scored.empty:
            metrics.append({"name": "Quiz average", "unit": "pts",
                            "values": scored.groupby("section")["quiz_score_num"].mean().to_dict()})
        wk = pl.week_attendance(cdb)
        if not wk.empty:
            metrics.append({"name": "Attendance", "unit": "%",
                            "values": (wk.assign(p=wk["attendance"].eq("+")).groupby("section")["p"].mean()
                                       * 100).to_dict()})
    for comp in cfgmod.workbook_components(course):
        t = tables.get(comp["workbook"])
        cols = [c for c in cfgmod.item_columns(comp) if t is not None and not t.empty and c in t.columns]
        if not cols or "Section" not in t.columns:
            continue
        vals = t[["Section"] + cols].copy()
        vals["Section"] = vals["Section"].map(clean_section)
        long = vals.melt(id_vars="Section", value_vars=cols, value_name="v")
        long["v"] = pd.to_numeric(long["v"], errors="coerce")
        long = long.dropna(subset=["v"])
        if long.empty:
            continue
        sections |= set(long["Section"])
        metrics.append({"name": f"{comp['label']} average", "unit": "pts",
                        "values": long.groupby("Section")["v"].mean().to_dict()})
    sections = sorted((s for s in sections if s), key=_sec_key)
    flagged = False
    for m in metrics:
        v = {s: m["values"][s] for s in sections if s in m["values"] and pd.notna(m["values"][s])}
        m["values"] = v
        m["spread"] = (max(v.values()) - min(v.values())) if len(v) > 1 else 0.0
        gap = att_gap if m["unit"] == "%" else score_gap
        m["flag"] = len(v) > 1 and m["spread"] >= gap
        flagged = flagged or m["flag"]
    metrics = [m for m in metrics if len(m["values"]) > 1]
    return {"sections": sections, "metrics": metrics, "flagged": flagged}


def one_line(result: dict) -> str:
    """Compact text: 'Quiz average 901 62.1 · 902 54.3 (spread 7.8) | Attendance 901 71% · 902 58% (spread 13 pp)'."""
    parts = []
    for m in result["metrics"]:
        fmt = (lambda x: f"{x:.0f}%") if m["unit"] == "%" else (lambda x: f"{x:.1f}")
        vals = " · ".join(f"{s} {fmt(v)}" for s, v in m["values"].items())
        spread = f"{m['spread']:.0f} pp" if m["unit"] == "%" else f"{m['spread']:.1f}"
        parts.append(f"{m['name']} {vals} (spread {spread}{', noticeable' if m['flag'] else ''})")
    return " | ".join(parts)
