"""Course Setup (terms, courses, assessment components) and Roster Sync tabs."""
from __future__ import annotations

import os
import re

import pandas as pd
import streamlit as st

from qs_core import config as cfgmod
from qs_core import excel_io as xio
from qs_core import gitops
from qs_core import roster as rmod
from qs_core import semester_import as semi
from qs_core import termclean
from qs_core.common import CONFIG_FILE, DB_FILE, path

from . import common as ui
from .grading_tabs import show_pre_push

NEW = "New course"
def _new_course_template(term: str) -> dict:
    return {"code": "", "title": "", "term": term, "sections": ["901", "902"], "weeks": 14, "show_standing": True,
            "workbooks": {}, "components": [
                {"label": "Quiz", "type": "quiz", "weight": 20, "count": 9, "rule": "best_n", "best_n": 7},
                {"label": "Homework", "type": "items", "weight": 20, "count": 5, "rule": "all",
                 "column_prefix": "HW #"},
                {"label": "Midterm", "type": "single", "weight": 20, "column": "Midterm"},
                {"label": "Final", "type": "single", "weight": 40, "column": "Final"},
            ]}


def term_block(cfg):
    terms = cfg["terms"] or [cfg.get("active_term") or ""]
    c1, c2, c3 = st.columns([2, 2, 1])
    active = c1.selectbox("Active term", terms,
                          index=terms.index(cfg["active_term"]) if cfg["active_term"] in terms else 0,
                          help="Used for backups, archives and as the default term of new courses.")
    new_term = c2.text_input("Add a term", placeholder="e.g. 2027-2028spring")
    norm = termclean.normalise_term(new_term) if new_term.strip() else ""
    if norm:
        if termclean.valid_term(norm):
            c2.caption(f"Will be saved as {norm}.")
        else:
            c2.caption("Use YYYY-YYYY followed by fall, spring or summer.")
    make_active = c3.checkbox("Make active", value=False, help="Switch the active term to the new term.")
    if c3.button("Save"):
        if norm:
            if not termclean.valid_term(norm):
                st.error("Use the format YYYY-YYYYfall / spring / summer with consecutive years, e.g. 2026-2027fall.")
                return
            if norm not in cfg["terms"]:
                cfg["terms"].append(norm)
            if make_active:
                active = norm
        cfg["active_term"] = active
        cfgmod.save(cfg)
        ui.refresh()
        ui.flash("success", f"Terms saved. Active term: {active}.")
        st.rerun()

    with st.expander("Remove a term entered by mistake"):
        st.caption("Removes the term from the configuration and deletes backups, archives and review queues named "
                   "after it. Spellings such as '2026-2027 spring' and '2026-2027_spring' are treated as the same term.")
        r1, r2 = st.columns([2, 1])
        options = [t for t in cfg["terms"]] + ["(type another spelling)"]
        pick = r1.selectbox("Term to remove", options, key="tc_pick")
        if pick == options[-1]:
            pick = r1.text_input("Term spelling", key="tc_text")
        if pick:
            found = termclean.find(pick)
            r2.metric("Files found", len(found["files"]))
            st.caption(f"In term list: {', '.join(found['terms']) or 'no'} · active: {'yes' if found['active'] else 'no'}"
                       f" · courses using it: {', '.join(found['courses']) or 'none'}")
            if found["files"]:
                st.dataframe(pd.DataFrame({"File": found["files"]}), use_container_width=True, hide_index=True,
                             height=min(240, 38 + 35 * len(found["files"])))
            others = [t for t in cfg["terms"] if termclean.normalise_term(t) != termclean.normalise_term(pick)]
            fb = st.selectbox("New active term (if needed)", others or [""], key="tc_fb")
            rm_courses = st.checkbox("Also remove the courses that use this term", value=False, key="tc_rmc")
            ok = st.checkbox(f"I want to remove '{pick}' and delete the files listed above", key="tc_ok")
            if st.button("Remove term", disabled=not ok, key="tc_go"):
                log = termclean.remove(pick, delete_files=True, remove_courses=rm_courses, fallback_term=fb)
                ui.refresh()
                ui.flash("success", " ".join(log))
                st.rerun()


def course_setup_tab(cfg):
    ui.page_header("Courses & Criteria", "Sections, evaluation criteria and weights for each course. These "
                   "settings drive the student profile, standing, What-If, analytics and the generated workbooks.")
    codes = cfgmod.all_courses(cfg)
    top1, top2 = st.columns([3, 2])
    pick = top1.selectbox("Course", [NEW] + codes, index=1 if codes else 0,
                          format_func=lambda x: x if x == NEW else f"{x} · {cfg['courses'][x].get('title', '')} "
                                                                  f"({cfg['courses'][x].get('term', '')})")
    if pick != NEW:
        cc = cfg["courses"][pick]
        top2.markdown(f'<div class="statusline"><span><b>Weights</b> '
                      f'{sum(x["weight"] for x in cc["components"]):g}%</span><span><b>Standing</b> '
                      f'{"shown" if cc.get("show_standing") else "hidden"}</span><span><b>Sections</b> '
                      f'{len(cc["sections"])}</span></div>', unsafe_allow_html=True)
    c = _new_course_template(cfg.get("active_term", "")) if pick == NEW else cfg["courses"][pick]
    if pick == NEW:
        cfgmod.normalise_course(c, "NEW0000")
    t_course, t_books, t_terms = st.tabs(["Course and criteria", "Workbooks", "Terms"])
    with t_terms:
        term_block(cfg)
    with t_books:
        if pick == NEW:
            st.caption("Save the new course first.")
        else:
            _workbooks_panel(cfg, pick, c)
    with t_course:
        _course_form(cfg, pick, c)


def _course_form(cfg, pick, c):
    if st.session_state.get("cfg_publish"):
        with st.container(border=True):
            show_pre_push([CONFIG_FILE], st.session_state["cfg_publish"], key="cfg")
            if st.button("Close preview", key="cfg_close"):
                st.session_state.pop("cfg_publish", None)
                st.rerun()
    with st.form(f"course_form_{pick}"):
        a1, a2, a3 = st.columns([1, 2, 1])
        code = a1.text_input("Course code:", value="" if pick == NEW else pick, disabled=pick != NEW,
                             placeholder="e.g. MCH2008").strip().upper()
        title = a2.text_input("Course title:", value=c.get("title", ""))
        terms = cfg["terms"] or [cfg.get("active_term", "")]
        term = a3.selectbox("Term:", terms, index=terms.index(c["term"]) if c.get("term") in terms else 0)
        b1, b2, b3 = st.columns([2, 1, 1])
        sections = b1.text_input("Sections (comma separated):", value=", ".join(c["sections"]))
        weeks = b2.number_input("Teaching weeks:", min_value=1, max_value=20, value=int(c["weeks"]))
        show = b3.checkbox("Show standing to students", value=bool(c.get("show_standing", True)))
        st.markdown("**Evaluation criteria** — tick the criteria this course uses and set their weights "
                    "(percent of the final grade).")
        h = st.columns([1.3, 0.7, 1, 0.9, 1.9, 0.8])
        for col, txt in zip(h, ["Criterion", "Used", "Weight (%)", "Items", "Counting rule", "Best N"]):
            col.markdown(f"<div class='section-label' style='margin:0'>{txt}</div>", unsafe_allow_html=True)
        crit_in = {}
        rules = list(cfgmod.RULES)
        for name in cfgmod.CRITERIA_ORDER:
            ex = next((x for x in c["components"] if cfgmod.criterion_of(x) == name), None)
            r = st.columns([1.3, 0.7, 1, 0.9, 1.9, 0.8])
            r[0].markdown(f"**{name}**")
            used = r[1].checkbox(f"Use {name}", value=ex is not None, key=f"cr_use_{pick}_{name}",
                                 label_visibility="collapsed")
            weight = r[2].number_input(f"{name} weight", 0.0, 100.0, float(ex["weight"]) if ex else 0.0, 0.5,
                                       key=f"cr_w_{pick}_{name}", label_visibility="collapsed")
            default_n = (ex.get("count", 1) if ex else {"Quiz": 9, "Homework": 5, "Experiment": 6}.get(name, 1))
            count = r[3].number_input(f"{name} items", 1, 30, int(default_n), 1, key=f"cr_n_{pick}_{name}",
                                      label_visibility="collapsed",
                                      help="How many quizzes / assignments / projects / exams this criterion has.")
            cur_rule = (ex or {}).get("rule", "best_n" if name == "Quiz" else "all")
            rule = r[4].selectbox(f"{name} rule", rules, index=rules.index(cur_rule) if cur_rule in rules else 1,
                                  format_func=cfgmod.RULES.get, key=f"cr_r_{pick}_{name}",
                                  label_visibility="collapsed")
            best = r[5].number_input(f"{name} best N", 1, 30, int((ex or {}).get("best_n") or min(7, count)), 1,
                                     key=f"cr_b_{pick}_{name}", label_visibility="collapsed")
            crit_in[name] = (used, weight, count, rule, best, ex)
        st.caption("Items = 1 stores one score column (e.g. Midterm); more than one creates numbered columns "
                   "(HW #1 … HW #5). Experiment always has a score and a feedback column per experiment "
                   "(Exp #1, Exp #1 FB …). 'Best N' only applies to the 'Best N of M' rule. Each criterion has its "
                   "own course-coded workbook (e.g. <CODE>_experiments.xlsx, <CODE>_finals.xlsx); quizzes and "
                   "experiments can be graded with Claude.")
        custom = [x for x in c["components"] if cfgmod.criterion_of(x) is None]
        if custom:
            st.caption("Kept unchanged (not one of the six standard criteria): "
                       + ", ".join(f"{x['label']} {x['weight']:g}%" for x in custom))
        publish = st.checkbox("Show the pre-push preview after saving", value=True)
        saved = st.form_submit_button("Save course", type="primary")

    if saved:
        if pick == NEW and (not code or code in cfg["courses"]):
            st.error("Enter a new, unique course code." if not code else f"{code} already exists.")
            return
        code = code if pick == NEW else pick
        new = {"code": code, "title": title.strip(), "term": term, "weeks": int(weeks), "show_standing": show,
               "sections": [s.strip() for s in sections.split(",") if s.strip()],
               "workbooks": {} if pick == NEW else dict(c["workbooks"]),
               "components": [cfgmod.build_criterion(n, code, w, cnt, rl, bn, existing=ex)
                              for n, (u, w, cnt, rl, bn, ex) in crit_in.items() if u]
                             + [dict(x) for x in c["components"] if cfgmod.criterion_of(x) is None]}
        cfgmod.normalise_course(new, code)
        problems = cfgmod.validate_course(new)
        hard = [p for p in problems if "weights add up" not in p]
        if hard:
            for p in hard:
                st.error(p)
            return
        for p in problems:
            st.warning(p)
        cfg["courses"][code] = new
        cfgmod.save(cfg)
        ui.refresh()
        msg = f"Saved {code}: {cfgmod.weight_summary(new) or 'no weighted components'}."
        ui.mark_pending(CONFIG_FILE)
        if publish:
            st.session_state["cfg_publish"] = f"Course setup: {code}"
        ui.flash("success", msg)
        st.rerun()

    if pick == NEW:
        return
    for p in cfgmod.validate_course(c):
        st.warning(p)


def _workbooks_panel(cfg, pick, c):
    rows = []
    for f in cfgmod.course_files(c):
        exists = os.path.exists(path(f))
        n = len(xio.read_students(f)) if exists else 0
        rows.append({"Workbook": f, "Exists": "Yes" if exists else "No", "Students": n,
                     "Published to portal": "Yes" if f in cfgmod.published_course_files(c) else "No (local only)"})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    missing = [r["Workbook"] for r in rows if r["Exists"] == "No"]
    if missing:
        current = rmod.existing_students(c)
        if current:
            st.info(f"{len(missing)} workbook(s) do not exist yet: {', '.join(missing)}. They can be created now "
                    f"from the current class list ({len(current)} students) with the template formatting.")
            if st.button("Create the missing workbooks", key=f"mk_{pick}"):
                ros = pd.DataFrame([{k: v[k] for k in ("Student ID", "Full Name", "Section", "Department")}
                                    for v in current.values()])
                try:
                    log = rmod.apply_roster(c, ros)
                    ui.flash("success", " ".join(l for l in log if l.startswith("Created")) or "Nothing to create.")
                except ValueError as e:
                    ui.flash("error", str(e))
                ui.refresh()
                st.rerun()
        else:
            st.info("Missing workbooks are generated with exact template formatting when you import the class "
                    "roster: " + ", ".join(missing))

    with st.expander("Template workbooks"):
        st.write("New workbooks, sheets, columns and rows clone the formatting of these templates "
                 "(row heights, column widths, borders, fonts, number formats, fills and alignments).")
        for k, name in xio.TEMPLATES.items():
            p = path("templates", name)
            st.write(f"- `templates/{name}` — {'present' if os.path.exists(p) else 'missing'} "
                     f"(cloned from `{xio.TEMPLATE_SOURCES[k]}`)")
        if st.button("Rebuild templates from the base workbooks"):
            made = xio.ensure_templates(force=True)
            ui.flash("success", f"Rebuilt {len(made)} template(s).")
            st.rerun()

    with st.expander("Remove this course from the configuration"):
        st.write("Only the configuration entry is removed. Workbooks and portal records stay on disk "
                 "(archive them first if the term is over).")
        ok = st.checkbox(f"I want to remove {pick}", key=f"del_{pick}")
        if st.button("Remove course", disabled=not ok):
            del cfg["courses"][pick]
            cfgmod.save(cfg)
            ui.refresh()
            ui.flash("success", f"Removed {pick} from the configuration.")
            st.rerun()


# ----------------------------------------------------------------------------- roster
def semester_import_section(cfg):
    st.caption("Reads every university grade-report export in this folder (`Öğretim Elemanı Not Excel Raporu.xlsx`, "
             "`... (1).xlsx`, `... (2).xlsx`, …) or uploaded here, groups the students by course code and creates "
             "or updates `<COURSE>_attendance.xlsx`, `<COURSE>_grades.xlsx` (one worksheet per section, named "
             "exactly after the section) and the roster in `grades_database.xlsx`. Template formatting is cloned "
             "and existing grades and attendance are kept.")
    log = st.session_state.get("semi_log")
    if log:
        st.success("Import finished. Every changed file was backed up first.")
        for line in log:
            st.write("• " + line)
        with st.container(border=True):
            show_pre_push(gitops.publishable_files(ui.config()), "Semester roster import", key="semi")
        if st.button("Dismiss", key="semi_dismiss"):
            st.session_state.pop("semi_log", None)
            st.rerun()

    found = semi.find_report_files()
    names = [os.path.relpath(p, path()) for p in found]
    picked = st.multiselect("Report files found in the folder (root and input/rosters/):", names, default=names,
                            key="semi_files")
    ups = st.file_uploader("Or upload report file(s):", type=["xlsx"], accept_multiple_files=True, key="semi_up")
    sources = [(os.path.basename(n), open(path(n), "rb").read()) for n in picked]
    sources += [(u.name, u.getvalue()) for u in (ups or [])]
    if not sources:
        st.caption("Put the exported files into the quiz_system_claude folder (or input/rosters/) and reload, "
                   "or upload them above.")
        return
    o1, o2, o3, o4 = st.columns(4)
    group_col = o1.radio("Section sheets from:", rmod.SECTION_COLUMNS, horizontal=True, key="semi_group")
    terms = cfg["terms"] or [cfg.get("active_term", "")]
    term = o2.selectbox("Term:", terms, index=terms.index(cfg["active_term"]) if cfg["active_term"] in terms else 0,
                        key="semi_term")
    weeks = o3.number_input("Weeks (new courses):", 1, 20, 14, key="semi_weeks")
    quizzes = o4.number_input("Quizzes (new courses):", 1, 30, 9, key="semi_quiz")
    res = semi.read_reports(sources, group_col)
    for name, n, codes in res.files:
        st.caption(f"{name}: {n} student row(s), course(s) {', '.join(codes) or 'none'}.")
    for w in res.warnings:
        st.warning(w)
    if not res.courses:
        return
    prev = pd.DataFrame(semi.preview(cfg, res))
    prev.insert(0, "Import", True)
    edited = st.data_editor(prev, hide_index=True, use_container_width=True, key=f"semi_prev_{group_col}",
                            disabled=[c for c in prev.columns if c != "Import"],
                            column_config={"Import": st.column_config.CheckboxColumn(help="Create or update this course")})
    chosen = [r["Course"] for _, r in edited.iterrows() if r["Import"]]
    for code in chosen:
        c = cfgmod.course(cfg, code)
        if c and c.get("roster_section_column", "Section") != group_col and rmod.existing_students(c):
            st.warning(f"{code} is grouped by '{c.get('roster_section_column', 'Section')}'. Importing it grouped by "
                       f"'{group_col}' moves students into different section sheets (their records move with them).")
    remove = st.checkbox("Remove students who are no longer on the report", value=False, key="semi_remove",
                         help="Off by default: dropped students keep their rows and records.")
    if st.button(f"Generate / update workbooks for {len(chosen)} course(s)", type="primary", disabled=not chosen):
        with st.spinner("Creating and updating workbooks..."):
            try:
                st.session_state["semi_log"] = semi.apply(cfg, res, chosen, term, group_col, int(weeks),
                                                          int(quizzes), remove_dropped=remove)
                ui.mark_pending(*gitops.publishable_files(cfg))
            except Exception as e:  # noqa: BLE001
                st.session_state["semi_log"] = [f"Import failed: {e}"]
        ui.refresh()
        st.rerun()


def roster_tab(cfg):
    ui.page_header("Rosters", "Start-of-semester import from the university grade report, and roster updates "
                   "during the semester. Existing grades and attendance are always kept.")
    semester_import_section(cfg)
    st.divider()
    # Secondary path, collapsed by default: any other roster file (one file per section, generic headers)
    if st.toggle("Manual roster sync (other roster files)", value=bool(st.session_state.get("roster_log")),
                 key="ro_manual", help="For roster files that are not the university grade-report export."):
        _roster_sync(cfg)


def _roster_sync(cfg):
    st.caption("Upload the class roster exported from the school system (one file with a section column, or one "
             "file per section). The first upload creates every course-coded workbook with the template "
             "formatting; later uploads keep all grades, feedback and attendance and report new students, "
             "dropped students and section changes before anything is written.")
    codes = cfgmod.all_courses(cfg)
    if not codes:
        st.info("Create a course in Course Setup first.")
        return
    if st.session_state.get("roster_log") and st.session_state.get("roster_code") in cfg["courses"]:
        _roster_log(cfg)
    code = st.selectbox("Course:", codes, key="ro_course")
    c = cfg["courses"][code]
    saved_col = c.get("roster_section_column", "Section")
    g1, g2 = st.columns([2, 1])
    group_col = g1.radio("Group students into section sheets by:", rmod.SECTION_COLUMNS,
                         index=rmod.SECTION_COLUMNS.index(saved_col), horizontal=True, key=f"ro_group_{code}",
                         help="The university grade report has both a lecture 'Section' and a 'Lab Section' column. "
                              "The choice is saved with the course so later roster updates group students the same way.")
    only_course = g2.checkbox("Only import rows for this course", value=True, key=f"ro_only_{code}",
                              help="Uses the course-code column of the file when it has one.")
    files = st.file_uploader("Class roster file(s):", type=["xlsx", "xls", "csv"], accept_multiple_files=True,
                             key=f"ro_files_{code}")
    if not files:
        st.caption("Supported: the university instructor grade report export (sheet 'Worksheet', with "
                   "student number, first name, last name, program, course code, Section and Lab Section "
                   "columns) and generic rosters with English or Turkish headers.")
        return
    results = []
    for f in files:
        opts = ["(from the file)"] + c["sections"]
        guess = next((s for s in c["sections"] if s in f.name), None)
        default = st.selectbox(f"Section for students in **{f.name}** without a section column:", opts,
                               index=opts.index(guess) if guess else 0, key=f"ro_sec_{code}_{f.name}")
        try:
            res = rmod.parse_roster(f.getvalue(), f.name, None if default == opts[0] else default,
                                    section_column=group_col, course_code=code if only_course else None)
        except ValueError as e:
            st.error(f"{f.name}: {e}")
            continue
        mapping = ", ".join(f"{k} ← '{v}'" for k, v in res.mapping.items())
        secs = res.students["Section"].value_counts()
        sec_txt = ", ".join(f"{k} ({v})" for k, v in sorted(secs.items(), key=lambda kv: xio.section_sort_key(kv[0])))
        codes_txt = f" Courses in file: {', '.join(f'{k} ({v})' for k, v in res.course_codes.items())}." \
            if res.course_codes else ""
        st.caption(f"{f.name}: {res.format} · sheet '{res.sheet}', header row {res.header_row}, "
                   f"{len(res.students)} students imported. Columns: {mapping}.{codes_txt} "
                   f"Grouped by '{res.section_column}': {sec_txt or 'none'}.")
        for w in res.warnings:
            st.warning(f"{f.name}: {w}")
        results.append(res)
    roster = rmod.combine(results)
    if roster.empty:
        return
    if (roster["Section"] == "").any():
        st.error("Some students still have no section. Choose a section for that file above.")
        return
    has_books = bool(rmod.existing_students(c))
    if has_books and group_col != saved_col:
        st.warning(f"{code} was set up with students grouped by '{saved_col}'. Grouping by '{group_col}' now "
                   f"would report almost every student as a section change and move their rows (grades and "
                   f"attendance move with them). Switch back unless this is intended.")
    unknown_secs = sorted(set(roster["Section"]) - set(c["sections"]), key=xio.section_sort_key)
    add_secs = False
    if unknown_secs:
        st.warning(f"The roster contains section(s) not defined for {code}: {', '.join(unknown_secs)}. "
                   f"A formatted section sheet is created for each of them.")
        add_secs = st.checkbox("Add the new section(s) to the course configuration", value=True,
                               key=f"ro_addsec_{code}")
    with st.expander(f"Roster preview ({len(roster)} students)"):
        st.dataframe(roster, use_container_width=True, hide_index=True)

    d = rmod.diff_roster(c, roster)
    st.markdown("##### Changes before applying")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("New students", len(d.added))
    m2.metric("Dropped / missing", len(d.dropped))
    m3.metric("Section changes", len(d.section_changes))
    m4.metric("Name corrections", len(d.name_changes))
    m5.metric("Unchanged", d.unchanged)
    if d.missing_files:
        st.info("These workbooks do not exist yet and will be generated from the templates: "
                + ", ".join(d.missing_files))
    if d.added:
        st.markdown("**Newly added students**")
        st.dataframe(pd.DataFrame(d.added), use_container_width=True, hide_index=True)
    if d.dropped:
        st.markdown("**Dropped / missing from the roster**")
        st.dataframe(pd.DataFrame(d.dropped), use_container_width=True, hide_index=True)
    if d.section_changes:
        st.markdown("**Section changes**")
        st.dataframe(pd.DataFrame([{"Student ID": x["Student ID"], "Full Name": x["Full Name"],
                                    "Change": f"{x['from']} -> {x['to']}"} for x in d.section_changes]),
                     use_container_width=True, hide_index=True)
    if d.name_changes:
        st.markdown("**Name corrections**")
        st.dataframe(pd.DataFrame(d.name_changes).rename(columns={"old": "Current name", "new": "Roster name"}),
                     use_container_width=True, hide_index=True)
    if not d.has_changes:
        st.success("The workbooks already match this roster.")
        return

    o1, o2, o3 = st.columns(3)
    remove = o1.checkbox("Remove dropped students", value=False,
                         help="Off by default: dropped students keep their rows and records.")
    names = o2.checkbox("Apply name corrections", value=True)
    upd_db = o3.checkbox(f"Update sections/names in {DB_FILE}", value=True)
    confirm = True
    if remove and d.dropped:
        confirm = st.checkbox(f"I confirm deleting {len(d.dropped)} student row(s) (a backup is kept).")
    if st.button("Apply roster changes", type="primary", disabled=not confirm):
        if add_secs or group_col != saved_col:
            if add_secs:
                c["sections"] = sorted(set(c["sections"]) | set(unknown_secs), key=xio.section_sort_key)
            c["roster_section_column"] = group_col
            cfgmod.save(cfg)
        with st.spinner("Updating workbooks..."):
            try:
                log = rmod.apply_roster(c, roster, remove_dropped=remove, update_names=names,
                                        update_database=upd_db)
            except ValueError as e:
                st.error(str(e))
                return
        ui.refresh()
        st.session_state["roster_log"] = log
        ui.mark_pending(*[f for f in cfgmod.published_course_files(c) + [DB_FILE]])
        st.session_state["roster_code"] = code
        if add_secs or group_col != saved_col:
            log.append(f"Saved to the course configuration: grouping by '{group_col}', sections "
                       f"{', '.join(c['sections'])}.")
        st.rerun()


def _roster_log(cfg):
    st.success("Roster applied. Every file was backed up first.")
    for line in st.session_state["roster_log"]:
        st.write("• " + line)
    pub = [f for f in cfgmod.published_course_files(cfg['courses'][st.session_state['roster_code']]) + [DB_FILE] if os.path.exists(path(f))]
    with st.container(border=True):
        show_pre_push(pub, f"Roster sync: {st.session_state['roster_code']}", key="ro")
    if st.button("Dismiss", key="ro_dismiss"):
        st.session_state.pop("roster_log", None)
        st.rerun()
    st.caption("Published files: " + ", ".join(pub) + ". Attendance and grade workbooks stay local "
               "(they are git-ignored).")
