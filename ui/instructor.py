"""Instructor dashboard: login, status strip, Analytics, Announcements & No Class This Week.
Other tabs live in setup_tabs.py, grading_tabs.py and admin_tabs.py."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import portal_logic as pl
from qs_core import announcements as annmod
from qs_core import config as cfgmod
from qs_core.common import ANNOUNCEMENTS_FILE, STATUS_FILE, path

from . import common as ui


def instructor_password():
    # Set INSTRUCTOR_PASSWORD in Streamlit Secrets (or as an environment variable).
    return ui.get_secret("INSTRUCTOR_PASSWORD") or "a123b456c789++"


def login() -> bool:
    if st.session_state.get("instructor_auth"):
        if st.sidebar.button("Logout"):
            st.session_state.instructor_auth = False
            st.rerun()
        return True
    pwd = st.text_input("Enter Instructor Password:", type="password")
    if st.button("Login"):
        if pwd == instructor_password():
            st.session_state.instructor_auth = True
            st.rerun()
        else:
            st.error("Invalid password. Access denied.")
    return False


def status_strip(cfg):
    import portal_logic as pl
    from qs_core.common import DB_FILE, META_FILE, path as _path
    upd = pl.last_updated(_path(DB_FILE), _path(META_FILE))
    active = sum(1 for a in annmod.load() if a.get("active"))
    _all, _db, status_df, _stats, _hidden = ui.database()
    ui.metric_grid([
        ui.metric_card("Active term", cfg.get("active_term") or "—"),
        ui.metric_card("Courses", str(len(cfgmod.courses_for_term(cfg))), ", ".join(cfgmod.courses_for_term(cfg))),
        ui.metric_card("Portal data updated", upd.strftime("%d %b %Y, %H:%M") if upd else "—", "Istanbul time"),
        ui.metric_card("Active announcements", str(active), f"{len(status_df)} No Class entr{'y' if len(status_df) == 1 else 'ies'}"),
    ])


# ----------------------------------------------------------------------------- analytics
def analytics_tab(cfg):
    df_all, df_db, status_df, _stats, _hidden = ui.database()
    courses = sorted(set(cfgmod.all_courses(cfg)) | (set(df_all["course_code"].dropna()) if not df_all.empty else set()))
    if not courses:
        st.info("No courses yet. Create one in Course Setup.")
        return
    sel = st.selectbox("Select Course to Analyze:", courses, key="an_course")
    c = cfgmod.course(cfg, sel)

    if c:
        st.caption(f"{c.get('title') or ''} · Term {c.get('term') or '—'} · Sections {', '.join(c['sections'])} · "
                   f"{cfgmod.weight_summary(c) or 'No weighted components'}")
        for comp in cfgmod.workbook_components(c):
            t = ui.table(comp["workbook"])
            if t.empty:
                st.caption(f"{comp['label']}: workbook {comp['workbook']} not found or empty.")
                continue
            cols = [x for x in cfgmod.item_columns(comp) if x in t.columns]
            summary = []
            for col in cols:
                vals = pd.to_numeric(t[col], errors="coerce").dropna()
                summary.append({"Item": col, "Graded": len(vals),
                                "Average": round(vals.mean(), 1) if len(vals) else None,
                                "Median": round(vals.median(), 1) if len(vals) else None,
                                "Highest": vals.max() if len(vals) else None,
                                "Lowest": vals.min() if len(vals) else None})
            if not summary or all(s["Graded"] == 0 for s in summary):
                st.caption(f"{comp['label']} ({comp['weight']:g}%): no scores entered yet.")
                continue
            st.subheader(f"{comp['label']} Overview ({comp['weight']:g}% weight)")
            if comp["type"] == "single" and summary:
                s = summary[0]
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Evaluated Students", s["Graded"])
                m2.metric("Average Score", f"{s['Average']:.1f} / 100")
                m3.metric("Median Score", f"{s['Median']:.1f} / 100")
                m4.metric("Highest / Lowest", f"{s['Highest']:.0f} / {s['Lowest']:.0f}")
                if "Section" in t.columns:
                    by = (t.assign(_v=pd.to_numeric(t[comp["column"]], errors="coerce")).dropna(subset=["_v"])
                          .groupby("Section")["_v"].agg(["count", "mean"]).reset_index())
                    by.columns = ["Section", "Evaluated", "Average"]
                    by["Average"] = by["Average"].round(1)
                    st.dataframe(by, use_container_width=True, hide_index=True)
            else:
                st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)
            st.divider()

    if c:
        from qs_core import sectioncmp
        cmp_ = sectioncmp.compare(c, df_db, ui.course_tables(c))
        if cmp_["metrics"]:
            st.markdown(f'<div class="statusline{" flagged" if cmp_["flagged"] else ""}"><span><b>Sections</b></span>'
                        f'<span>{ui.esc(sectioncmp.one_line(cmp_))}</span></div>', unsafe_allow_html=True)
            if cmp_["flagged"]:
                st.caption(f"Noticeable: a spread of at least {sectioncmp.SCORE_GAP:g} points in an average score or "
                           f"{sectioncmp.ATT_GAP:g} percentage points in attendance between sections.")
        missing_entries_summary(c, df_db)
        st.divider()

    course_db = df_db[df_db["course_code"] == sel] if not df_db.empty else pd.DataFrame()
    if course_db.empty:
        st.info(f"No weekly records uploaded for `{sel}` yet.")
        return
    ordered = course_db.sort_values("week_no", na_position="last")
    weeks = ordered["week"].dropna().unique().tolist()
    sel_week = st.selectbox("Select Academic Week:", weeks, index=len(weeks) - 1)
    week_data = course_db[course_db["week"] == sel_week].copy()
    wk_no = pl.week_number(sel_week)
    cancelled = status_df[(status_df["course_code"] == sel) & (status_df["week_no"] == wk_no)]
    if not cancelled.empty:
        st.warning(f"No class this week for Section(s): {', '.join(cancelled['section'])}. These sections are "
                   f"excluded from the statistics below.")
    st.subheader(f"Analytics Summary: {sel} - {sel_week}")
    wk_att = pl.week_attendance(week_data)
    total = len(wk_att)
    att = int((wk_att["attendance"] == "+").sum())
    scored = week_data[week_data["quiz_n"].notna() & week_data["quiz_score_num"].notna()]
    qtable = (scored.groupby("quiz_n")["quiz_score_num"].agg(["count", "mean", "median"]).reset_index()
              if not scored.empty else pd.DataFrame())
    nq = len(qtable)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Enrolled Students", total)
    m2.metric("Attendance", f"{att} ({(att / total * 100) if total else 0:.1f}%)")
    m3.metric("Quizzes This Week", nq)
    m4.metric("Class Average", f"{qtable.iloc[0]['mean']:.2f} / 100" if nq == 1 else ("See table" if nq else "N/A"))
    if nq:
        st.dataframe(pd.DataFrame({
            "Quiz": [f"Quiz {int(q)}" for q in qtable["quiz_n"]], "Evaluated": qtable["count"].astype(int),
            "Average": qtable["mean"].round(1), "Median": qtable["median"].round(1)}),
            use_container_width=True, hide_index=True)
    if not wk_att.empty:
        by_sec = wk_att.groupby("section")["attendance"].apply(lambda s: f"{(s == '+').sum()}/{len(s)}")
        st.caption("Attendance by section: " + " · ".join(f"{k}: {v}" for k, v in by_sec.items()))
    st.divider()
    st.markdown("#### Weekly Roster")
    cols = ["student_id", "full_name", "section", "attendance", "quiz_no", "quiz_score", "feedback"]
    if week_data["attended_section"].astype(str).str.strip().ne("").any():
        cols.insert(3, "attended_section")
    view = week_data[cols].rename(columns={
        "student_id": "Student ID", "full_name": "Full Name", "section": "Section",
        "attended_section": "Attended Section", "attendance": "Attendance", "quiz_no": "Quiz",
        "quiz_score": "Score", "feedback": "Feedback"})
    st.dataframe(view, use_container_width=True, hide_index=True)


def missing_entries_summary(c, df_db):
    """Course/column-level coverage of every criterion item. Deliberately no per-student detail."""
    from qs_core import coverage
    tables = ui.course_tables(c)
    cov = coverage.course_coverage(c, df_db, tables, ui.roster_sheet())
    st.subheader("Missing entries")
    st.caption("Class-wide, per assessment column: how many enrolled students have a score entered. Items that "
               "nobody has a score for yet are shown as not started. Quiz scores of weeks marked No Class are "
               "not counted.")
    if cov.empty:
        st.info("This course has no evaluation criteria yet.")
        return
    started = cov[cov["Status"] != "Not started"]
    entered, expected = int(started["Entered"].sum()), int(started["Enrolled"].sum())
    ui.metric_grid([
        ui.metric_card("Items with missing entries", str(int((cov["Status"] == "Missing entries").sum())),
                       f"of {int(len(started))} started item(s)"),
        ui.metric_card("Missing cells", f"{int(started['Missing'].sum()):,}", "in started items"),
        ui.metric_card("Coverage of started items", f"{(100 * entered / expected) if expected else 0:.1f}%",
                       f"{entered:,} of {expected:,} cells entered"),
        ui.metric_card("Not started", str(int((cov["Status"] == "Not started").sum())), "items without any score"),
    ])
    f1, f2 = st.columns([2, 1])
    crit = f1.multiselect("Assessments", list(dict.fromkeys(cov["Assessment"])),
                          default=list(dict.fromkeys(cov["Assessment"])), key=f"cov_crit_{c['code']}")
    only = f2.checkbox("Only items with missing entries", value=False, key=f"cov_only_{c['code']}")
    view = cov[cov["Assessment"].isin(crit)]
    if only:
        view = view[view["Status"] == "Missing entries"]
    st.dataframe(view, use_container_width=True, hide_index=True,
                 column_config={"Coverage": st.column_config.ProgressColumn("Coverage", min_value=0.0, max_value=100.0,
                                                                            format="%.0f%%")})


# ----------------------------------------------------------------------------- announcements
def announcements_section(cfg):
    st.subheader("Announcements")
    st.write("Announcements appear in the student portal feed (latest "
             f"{cfg['settings'].get('announcement_feed_size', 5)} relevant active items) with a **New** badge "
             "until each student has seen them.")
    items = annmod.load()
    courses = cfgmod.all_courses(cfg)

    def scope_inputs(prefix, a=None):
        scopes = list(annmod.SCOPES)
        scope = st.radio("Target:", scopes, format_func=annmod.SCOPES.get, horizontal=True,
                         index=scopes.index(a["scope"]) if a else 0, key=f"{prefix}_scope")
        course, sections = "", []
        if scope != "all":
            if not courses:
                st.warning("Create a course first.")
            else:
                course = st.selectbox("Course:", courses, key=f"{prefix}_course",
                                      index=courses.index(a["course"]) if a and a["course"] in courses else 0)
        if scope == "sections" and course:
            secs = cfgmod.course(cfg, course)["sections"]
            sections = st.multiselect("Section(s):", secs, key=f"{prefix}_secs",
                                      default=[s for s in (a["sections"] if a else secs) if s in secs])
        return scope, course, sections

    with st.expander("New announcement", expanded=not items):
        title = st.text_input("Title:", key="ann_new_title", max_chars=120)
        body = st.text_area("Message:", key="ann_new_body", max_chars=2000)
        scope, course, sections = scope_inputs("ann_new")
        active = st.checkbox("Active (visible to students)", value=True, key="ann_new_active")
        push = True
        if st.button("Create announcement", type="primary"):
            if not title.strip() or not body.strip():
                st.error("Enter a title and a message.")
            elif scope != "all" and not course:
                st.error("Select a course.")
            elif scope == "sections" and not sections:
                st.error("Select at least one section.")
            else:
                annmod.create(items, title, body, scope, course, sections, active)
                annmod.save(items, cfg.get("active_term"))
                _after_ann_save(push, f"Announcement: {title.strip()[:60]}")
                st.rerun()

    if not items:
        st.caption("No announcements yet.")
        return
    st.markdown("##### Existing announcements")
    for a in sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True):
        state = "Active" if a["active"] else "Inactive"
        with st.expander(f"{state} · {a['title']} · {annmod.scope_label(a)} · {a['updated_at'][:16].replace('T', ' ')}"):
            p = f"ann_{a['id']}"
            title = st.text_input("Title:", a["title"], key=f"{p}_title", max_chars=120)
            body = st.text_area("Message:", a["body"], key=f"{p}_body", max_chars=2000)
            scope, course, sections = scope_inputs(p, a)
            active = st.checkbox("Active (visible to students)", value=a["active"], key=f"{p}_active")
            push = True
            b1, b2, b3 = st.columns(3)
            if b1.button("Save changes", key=f"{p}_save"):
                annmod.update(items, a["id"], title=title.strip(), body=body.strip(), scope=scope,
                              course=course, sections=sections, active=active)
                annmod.save(items, cfg.get("active_term"))
                _after_ann_save(push, f"Update announcement: {title.strip()[:60]}")
                st.rerun()
            if b2.button("Deactivate" if a["active"] else "Activate", key=f"{p}_toggle"):
                annmod.update(items, a["id"], active=not a["active"])
                annmod.save(items, cfg.get("active_term"))
                _after_ann_save(push, f"{'Deactivate' if a['active'] else 'Activate'} announcement")
                st.rerun()
            confirm = b3.checkbox("Confirm delete", key=f"{p}_confirm")
            if b3.button("Delete", key=f"{p}_delete", disabled=not confirm):
                items[:] = annmod.delete(items, a["id"])
                annmod.save(items, cfg.get("active_term"))
                _after_ann_save(push, "Delete announcement")
                st.rerun()


def _after_ann_save(push: bool, message: str):
    ui.refresh()
    ui.mark_pending(ANNOUNCEMENTS_FILE)
    st.session_state["ann_msg"] = message
    ui.flash("success", "Announcement saved. Review the pre-push preview below and confirm to publish it.")


def no_class_section(cfg):
    st.subheader("No Class This Week")
    st.write("Mark a week as **No Class** for selected course sections (holiday, instructor absence, etc.). "
             "Students see a notice with your note instead of an absence, and that week is excluded from "
             "attendance and quiz statistics. The total number of quizzes does not change.")
    df_all, _db, status_df, _s, _h = ui.database()
    courses = cfgmod.all_courses(cfg)
    if not courses and not df_all.empty:
        courses = sorted(set(df_all["course_code"]))
    if not courses:
        st.info("No courses yet.")
        return
    course = st.selectbox("Course:", courses, key="ns_course")
    c = cfgmod.course(cfg, course)
    sections = (c["sections"] if c else []) or sorted({s for s in df_all.loc[df_all["course_code"] == course,
                                                                              "section"] if s})
    weeks = df_all.loc[df_all["course_code"] == course, "week_no"].dropna() if not df_all.empty else []
    max_w = (c["weeks"] if c else 20) or 20
    guess = int(max(weeks)) + 1 if len(weeks) else 1
    with st.form("no_class_form"):
        week_no = st.number_input("Week number:", min_value=1, max_value=max(max_w, 20), value=min(guess, max_w), step=1)
        chosen = st.multiselect("Sections:", sections, default=sections)
        note = st.text_area("Note shown to students:", max_chars=500,
                            placeholder="e.g. No class due to the national holiday. The quiz is postponed to next week.")
        submitted = st.form_submit_button("Mark as No Class")
    if submitted:
        if not chosen:
            st.error("Select at least one section.")
        else:
            existing = 0
            if not df_all.empty:
                existing = int(((df_all["course_code"] == course) & (df_all["week_no"] == week_no)
                                & df_all["section"].isin(chosen)).sum())
            new_status = pl.add_status(status_df, course, int(week_no), chosen, note)
            pl.write_status(path(STATUS_FILE), new_status)
            ui.refresh()
            msg = f"Marked {course} Week {int(week_no)} as No Class for Section(s) {', '.join(chosen)}."
            if existing:
                msg += f" {existing} existing record(s) for this week are now hidden from students and statistics."
            ui.mark_pending(STATUS_FILE)
            st.session_state["ns_msg"] = f"No class: {course} Week {int(week_no)} Sections {', '.join(chosen)}"
            ui.flash("success", msg + " Review the pre-push preview below and confirm to publish it.")
            st.rerun()

    st.markdown("##### Current No Class entries")
    if status_df.empty:
        st.caption("No entries yet.")
        return
    view = status_df.rename(columns={"course_code": "Course", "week_no": "Week", "section": "Section",
                                     "note": "Note", "created_at": "Created"})
    st.dataframe(view, use_container_width=True, hide_index=True)
    labels = {f"{r.course_code} · Week {r.week_no} · Section {r.section}": (r.course_code, int(r.week_no), r.section)
              for r in status_df.itertuples()}
    picked = st.multiselect("Select entries to remove:", list(labels), key="ns_remove")
    if st.button("Remove selected") and picked:
        new_status = pl.remove_status(status_df, [labels[p] for p in picked])
        pl.write_status(path(STATUS_FILE), new_status)
        ui.refresh()
        n = len(picked)
        ui.mark_pending(STATUS_FILE)
        st.session_state["ns_msg"] = f"Remove {n} no-class entr{'y' if n == 1 else 'ies'}"
        ui.flash("success", f"Removed {n} entr{'y' if n == 1 else 'ies'}. Review the preview below and confirm to publish.")
        st.rerun()


# ----------------------------------------------------------------------------- main
def render():
    ui.page_header("Instructor Dashboard", "Analytics, announcements and No Class entries for the web portal.")
    if not login():
        return
    cfg = ui.config()
    ui.show_flash()
    status_strip(cfg)
    st.caption("Course setup, roster import, AI grading, review, grade revisions, backups, archives, billing and "
               "LAN testing are in the Local Desktop UI on the instructor's computer (run_desktop.bat).")
    tabs = st.tabs(["Analytics", "Announcements & No Class"])
    with tabs[0]:
        analytics_tab(cfg)
    with tabs[1]:
        announcements_section(cfg)
        st.divider()
        no_class_section(cfg)
        st.divider()
        from .grading_tabs import show_pre_push
        show_pre_push([ANNOUNCEMENTS_FILE, STATUS_FILE],
                      st.session_state.get("ann_msg") or st.session_state.get("ns_msg") or "Update announcements",
                      key="web_ann")
