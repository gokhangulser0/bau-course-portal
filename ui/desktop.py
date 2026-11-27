"""Local Desktop UI: everything that needs this computer (files, Claude API, Excel writes, git).

Pages: Overview · Courses & Criteria · Rosters · AI Grading · Review & Publish · Term Archive ·
       Archive, Backups & Publish · Billing & Settings
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from qs_core import archive as arch
from qs_core import backup as bk
from qs_core import billing
from qs_core import config as cfgmod
from qs_core import grader, lan, pyenv
from qs_core import term_archive as tarch
from qs_core import roster as rmod

from . import admin_tabs, db_sync_ui, grading_tabs, revision_ui, setup_tabs
from . import lan as lan_ui
from . import common as ui

PAGES = {
    "Overview": "Status of the term, grading runs, budget and backups.",
    "Courses & Criteria": "Open courses, set sections and the evaluation criteria with their weights.",
    "Rosters": "Import the university grade-report rosters and keep the course workbooks in sync.",
    "AI Grading": "Pre-flight cost estimate, budget warnings and live grading with Claude.",
    "Review & Publish": "Check flagged papers, re-grade selected ones, approve the results and push them.",
    "Grade Revision": "Change one student's score and feedback after a revision request, without Excel.",
    "Term Archive": "Attendance, quiz, experiment and question PDFs saved per course after grading.",
    "Archive, Backups & Publish": "Publishing, workbook sync from the database, end-of-semester .zip, stamped "
                                  "backups and rollback.",
    "Billing & Settings": "Claude API balance ledger and budget thresholds.",
    "LAN Test": "Open the web portal from a phone, tablet or another PC on the same network.",
}


def _guard() -> bool:
    if lan.on_streamlit_cloud():
        st.error("The Local Desktop UI only runs on your own computer. Open it with run_desktop.bat.")
        return False
    try:
        addr = (st.get_option("server.address") or "").strip()
    except Exception:
        addr = ""
    if addr not in lan.LOCAL_ONLY:
        st.warning("This Local Desktop UI is reachable from other devices on the network. Start it with "
                   "run_desktop.bat (bound to 127.0.0.1) to keep grading, billing and backups private.")
    return True


def overview(cfg):
    ui.page_header("Course Operations", f"Local Desktop UI · term {cfg.get('active_term') or '—'}")
    led = billing.load_ledger()
    queues = grader.list_queues()
    pending = [q for q in queues if q["status"] in ("pending_review", "stopped")]
    open_flags = sum(len(grader.open_flags(q)) for q in pending)
    last = bk.latest_backup()
    archives = arch.list_archives()
    ui.metric_grid([
        ui.metric_card("Courses this term", str(len(cfgmod.courses_for_term(cfg))),
                       ", ".join(cfgmod.courses_for_term(cfg)) or "None yet"),
        ui.metric_card("Runs waiting for review", str(len(pending)), f"{open_flags} open flag(s)"),
        ui.metric_card("Estimated API balance", f"${led['balance_usd']:.2f}",
                       f"Spent so far ${led['total_spent_usd']:.2f}"),
        ui.metric_card("Latest backup", last.pretty_time if last else "None yet", last.original if last else ""),
        ui.metric_card("Semester archives", str(len(archives)), archives[-1]["name"] if archives else "None yet"),
    ])

    q1, q2, q3, q4 = st.columns(4)
    q1.button("Grade papers", on_click=go, args=("AI Grading",), use_container_width=True, type="primary")
    q2.button(f"Review runs ({len(pending)})", on_click=go, args=("Review & Publish",), use_container_width=True)
    q3.button("Import rosters", on_click=go, args=("Rosters",), use_container_width=True)
    q4.button("Backups and archive", on_click=go, args=("Archive, Backups & Publish",), use_container_width=True)

    ui.section_label("Courses")
    rows = []
    for code in cfgmod.all_courses(cfg):
        c = cfg["courses"][code]
        n = len(rmod.existing_students(c))
        rows.append({"Course": code, "Title": c.get("title", ""), "Term": c.get("term", ""),
                     "Sections": ", ".join(c["sections"]), "Students": n,
                     "Criteria": cfgmod.weight_summary(c) or "No weights set",
                     "Standing shown": "Yes" if c.get("show_standing") else "No",
                     "Archived PDFs": len(tarch.list_course(code))})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No courses yet. Import the grade-report rosters on the Rosters page or create a course on "
                "Courses & Criteria.")

    from qs_core import consistency
    mism = consistency.check_all(cfg)
    if mism:
        st.markdown(f'<div class="statusline flagged"><span><b>Excel and database mismatch</b> '
                    f'{len(mism)} record(s)</span><span>Quiz scores, feedback or attendance differ between the course '
                    f'workbooks and grades_database.xlsx.</span></div>', unsafe_allow_html=True)
        with st.expander("Show the mismatching records"):
            mdf = pd.DataFrame(mism)
            st.dataframe(mdf.astype(str), use_container_width=True, hide_index=True)
            st.caption("grades_database.xlsx is the source of truth: Sync Workbooks from Database copies its values "
                       "into the course workbooks. Make manual edits in grades_database.xlsx, not in the workbooks.")
    else:
        st.markdown('<div class="statusline"><span><b>Excel and database</b> consistent</span></div>',
                    unsafe_allow_html=True)
    db_sync_ui.render(cfg, key="dbs_over", show_status=False)
    absent_zeros_panel(cfg)

    lines = section_lines(cfg)
    if lines:
        ui.section_label("Section comparison")
        for code, flagged, text in lines:
            st.markdown(f'<div class="statusline{" flagged" if flagged else ""}"><span><b>{code}</b></span>'
                        f'<span>{ui.esc(text)}</span></div>', unsafe_allow_html=True)

    if pending:
        ui.section_label("Waiting for review")
        st.dataframe(pd.DataFrame([{"Run": grader.queue_label(q), "Papers": len(q["papers"]),
                                    "Open flags": len(grader.open_flags(q))} for q in pending[:10]]),
                     use_container_width=True, hide_index=True)

    ui.section_label("Start commands")
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Local Desktop UI (this computer only)")
        st.code("run_desktop.bat" if pyenv.IS_WIN else "bash run_desktop.sh", language="bat")
    with c2:
        st.caption("Web portal for LAN testing (phones, tablets)")
        st.code("run_lan.bat" if pyenv.IS_WIN else "bash run_lan.sh", language="bat")


def absent_zeros_panel(cfg):
    """Absent students of a held Quiz / Experiment who still have an empty score: preview, then write 0."""
    from qs_core import absent_zeros as az
    from .grading_tabs import show_pre_push
    try:
        changes = az.plan(cfg)
    except Exception as e:  # noqa: BLE001
        st.warning(f"The absent-zero check could not run: {e}")
        return
    if changes:
        st.markdown(f'<div class="statusline flagged"><span><b>Absent without a score</b> {len(changes)} record(s)'
                    f'</span><span>Marked absent in a week with a quiz or experiment, but the score is empty. '
                    f'They should receive 0.</span></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="statusline"><span><b>Absent zeros</b> in sync</span></div>',
                    unsafe_allow_html=True)
    if st.button("Sync Absent Zeros", key="az_sync", disabled=not changes,
                 help="Re-checks attendance against the quiz and experiment scores and lists the absent students "
                      "who should receive 0. Nothing is written until you confirm."):
        st.session_state["az_open"] = True
    if changes and st.session_state.get("az_open"):
        with st.container(border=True):
            st.markdown(f"**{len(changes)} absent record(s) will receive 0**")
            st.dataframe(pd.DataFrame(az.summarize(changes)), use_container_width=True, hide_index=True)
            with st.expander("Show every record"):
                st.dataframe(pd.DataFrame(az.public(changes)).astype(str), use_container_width=True,
                             hide_index=True)
            st.caption(f"Files: {', '.join(az.files_of(changes))}. Each file gets a stamped backup first and only "
                       f"cell values change (template formatting stays as it is). A score that is already entered "
                       f"is never overwritten. Feedback note for quizzes and experiments: \"{az.ABSENT_NOTE}\"")
            ok = st.checkbox("I checked the list. Write these zeros.", key="az_ok")
            c1, c2 = st.columns([1, 4])
            if c1.button(f"Write {len(changes)} zero(s)", type="primary", disabled=not ok, key="az_apply"):
                try:
                    with st.spinner("Writing (stamped backups first)..."):
                        out = az.apply(cfg, changes)
                    ui.mark_pending(*out["publish"])
                    ui.refresh()
                    st.session_state["az_msg"] = f"Absent zeros: {out['zeros']} record(s)"
                    st.session_state["az_publish"] = out["publish"]
                    st.session_state.pop("az_open", None)
                    st.session_state.pop("az_ok", None)
                    ui.flash("success", f"{out['zeros']} absent record(s) now have 0. " + " ".join(out["log"]))
                except ValueError as e:
                    ui.flash("error", str(e))
                except Exception as e:  # noqa: BLE001
                    ui.flash("error", f"Writing failed: {e}")
                st.rerun()
            if c2.button("Cancel", key="az_cancel"):
                st.session_state.pop("az_open", None)
                st.rerun()
    if st.session_state.get("az_msg"):
        show_pre_push(st.session_state.get("az_publish") or [], st.session_state["az_msg"], key="az_push")


def section_lines(cfg):
    from qs_core import sectioncmp
    _all, df_db, _st, _s, _h = ui.database()
    out = []
    for code in cfgmod.courses_for_term(cfg):
        c = cfg["courses"][code]
        r = sectioncmp.compare(c, df_db, ui.course_tables(c))
        if r["metrics"]:
            out.append((code, r["flagged"], sectioncmp.one_line(r)))
    return out


def term_archive_page(cfg):
    ui.page_header("Term Archive", "PDFs saved automatically after each grading run, one folder per course.")
    st.caption("Folder: term_archive/<COURSE_CODE>/ · names: <course>_w<week>_<section>_<type>.pdf with type attendance, quiz, question, "
               "experiment or experiment-question. A second quiz or experiment in the same week and section gets its "
               "number appended (e.g. _quiz-q5.pdf, _experiment-e3.pdf). "
               "These files contain student work and are never published to GitHub.")
    codes = sorted(set(tarch.archived_courses()) | set(cfgmod.all_courses(cfg)))
    if not codes:
        st.info("Nothing archived yet.")
        return
    code = st.selectbox("Course:", codes, key="ta_course")
    items = tarch.list_course(code)
    if not items:
        st.info(f"No PDFs archived for {code} yet. They are saved when a grading run finishes.")
        return
    weeks = sorted({i["week"] for i in items if i["week"] is not None})
    w = st.selectbox("Week:", ["All weeks"] + weeks, format_func=lambda x: x if isinstance(x, str) else f"Week {x}",
                     key="ta_week")
    shown = [i for i in items if w == "All weeks" or i["week"] == w]
    st.dataframe(pd.DataFrame([{"File": i["file"], "Week": i["week"], "Section": i["section"],
                                "Type": (i["type"] or "").capitalize(), "Quiz": i["quiz"] or "",
                                "Pages": i["pages"], "Size (MB)": round(i["size_bytes"] / 1e6, 2),
                                "Source file(s)": i["sources"], "Saved": i["archived_at"][:16].replace("T", " ")}
                               for i in shown]), use_container_width=True, hide_index=True)
    pick = st.selectbox("Download:", [i["file"] for i in shown], key="ta_pick")
    it = next(i for i in shown if i["file"] == pick)
    with open(it["path"], "rb") as f:
        st.download_button(f"{pick}", f.read(), file_name=pick, mime="application/pdf", key=f"ta_dl_{pick}")
    st.caption(f"Saved in {os.path.relpath(tarch.course_dir(code), ui.path())}")


def go(page: str):
    """Button callback: switch the top navigation to another page."""
    st.session_state["desk_page"] = page


def render():
    cfg = ui.config()
    if "desk_page" not in st.session_state or st.session_state["desk_page"] not in PAGES:
        st.session_state["desk_page"] = "AI Grading"
    try:
        nav = st.container(key="topnav")        # key gives the bar its CSS class (Streamlit 1.39+)
    except TypeError:
        nav = st.container()
    with nav:
        page = st.radio("Section", list(PAGES), horizontal=True, key="desk_page", label_visibility="collapsed")
    st.caption(PAGES[page])
    st.sidebar.markdown("**Local Desktop UI**")
    st.sidebar.caption("Runs on this computer only (127.0.0.1). The web portal, app.py, keeps the Student View and "
                       "the Instructor Dashboard. Use the bar at the top to switch pages.")
    if not _guard():
        return
    ui.show_flash()
    if page == "Overview":
        overview(cfg)
    elif page == "Courses & Criteria":
        setup_tabs.course_setup_tab(cfg)
    elif page == "Rosters":
        setup_tabs.roster_tab(cfg)
    elif page == "AI Grading":
        grading_tabs.grading_tab(cfg)
    elif page == "Review & Publish":
        grading_tabs.review_tab(cfg)
    elif page == "Grade Revision":
        ui.page_header("Grade Revision", "Targeted edit of one existing record. Backed up first; published only "
                       "after the pre-push preview.")
        revision_ui.render(cfg, key="drev")
    elif page == "Term Archive":
        term_archive_page(cfg)
    elif page == "Archive, Backups & Publish":
        admin_tabs.archive_tab(cfg)
    elif page == "LAN Test":
        lan_ui.render()
    else:
        admin_tabs.billing_tab(cfg)
