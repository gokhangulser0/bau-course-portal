"""Grade & Feedback Revision: edit one existing score/feedback without opening Excel.
Used by the Local Desktop UI and the web Instructor Dashboard. It is a targeted editor (course -> section ->
item -> student), not a roster browser: only the selected student's record for the selected item is shown."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from qs_core import config as cfgmod
from qs_core import revision as rev

from . import common as ui


def render(cfg, key: str = "rev"):
    st.caption("Update one student's score (and, for quizzes and experiments, the feedback) after a revision "
               "request. A stamped backup is made "
               "first; the course workbook (formatting unchanged) and, for quizzes, the portal database are updated. "
               "Publish afterwards with the pre-push preview below.")
    codes = cfgmod.all_courses(cfg)
    if not codes:
        st.info("No courses yet.")
        return
    c1, c2, c3, c4 = st.columns([1.1, 0.8, 1.1, 2.2])
    code = c1.selectbox("Course", codes, key=f"{key}_course")
    c = cfg["courses"][code]
    section = c2.selectbox("Section", c["sections"] or ["901"], key=f"{key}_sec_{code}")
    items = rev.items(c)
    if not items:
        st.info("This course has no evaluation criteria yet.")
        return
    labels = [f"{i.criterion} · {i.label}" if i.criterion not in i.label else i.label for i in items]
    idx = c3.selectbox("Item", range(len(items)), format_func=lambda k: labels[k], key=f"{key}_item_{code}")
    item = items[idx]
    studs = rev.students(c, item, section)
    if not studs:
        st.warning(f"No students found for Section {section} in {item.workbook}. Import the roster or create the "
                   f"workbook first.")
        return
    sid = c4.selectbox("Student", [s["Student ID"] for s in studs], index=None,
                       placeholder="Select the student who requested the revision",
                       format_func=lambda x: next(f"{s['Student ID']} · {s['Full Name']}" for s in studs
                                                  if s["Student ID"] == x),
                       key=f"{key}_sid_{code}_{section}")
    if not sid:
        _publish(key)
        return
    cur = rev.current(c, item, sid)
    with st.container(border=True):
        st.markdown(f'<div class="statusline"><span><b>Current score</b> '
                    f'{cur["score"] if cur["score"] is not None else "not entered"}</span>'
                    f'<span><b>Source</b> {cur["source"] or "—"}</span><span><b>Item</b> {item.label}</span></div>',
                    unsafe_allow_html=True)
        with st.form(f"{key}_form_{code}_{item.label}_{sid}"):
            f1, f2 = st.columns([1, 3])
            score = f1.number_input("New score (0-100)", 0.0, 100.0,
                                    float(cur["score"]) if cur["score"] is not None else 0.0, 0.5)
            clear = f1.checkbox("Clear the score", value=False, help="Leaves the cell empty (e.g. excused).")
            if item.has_feedback:
                fb = f2.text_area("Feedback", cur["feedback"], height=110)
            else:
                fb = ""
                f2.caption(f"{item.criterion} has no written feedback; only the score is stored.")
            reason = st.text_input("Reason (kept in revision_log.json)", placeholder="e.g. Student request; part B "
                                                                                    "re-checked")
            ok = st.form_submit_button("Save revision", type="primary")
        if ok:
            try:
                out = rev.save(c, item, sid, None if clear else score, fb, reason)
                ui.mark_pending(*out["files"])
                ui.refresh()
                st.session_state[f"{key}_msg"] = f"Grade revision: {code} {item.label} ({section})"
                ui.flash("success", " ".join(out["log"]) + " Review the pre-push preview below and confirm.")
            except Exception as e:  # noqa: BLE001
                ui.flash("error", f"Revision failed: {e}")
            st.rerun()
    hist = [h for h in rev.history(code) if h["item"] == item.label and h["student_id"] == sid]
    if hist:
        st.caption("Earlier revisions of this record: " + " · ".join(
            f"{h['at'][:16].replace('T', ' ')} {h['old_score']} -> {h['new_score']}" for h in hist[:5]))
    _publish(key)


def _publish(key):
    """Pre-push preview for the files changed by revisions in this session."""
    from qs_core import gitops
    from .grading_tabs import show_pre_push
    if not st.session_state.get(f"{key}_msg"):
        return
    pending = ui.pending_files()
    targets = [f for f in gitops.publishable_files(ui.config()) if f in pending]
    st.divider()
    show_pre_push(targets or gitops.publishable_files(ui.config()), st.session_state[f"{key}_msg"],
                  key=f"{key}_push")
