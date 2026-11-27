"""AI Grading (pre-flight estimate, budget warnings, live tracking) and Review & Publish tabs."""
from __future__ import annotations

import hashlib
import os

import pandas as pd
import streamlit as st

from qs_core import billing
from qs_core import config as cfgmod
from qs_core import gitops
from qs_core import grader
from qs_core.common import DB_FILE, INPUT_DIR, META_FILE, RUBRIC_FILE, clean_id, path

from . import common as ui


# ----------------------------------------------------------------------------- inputs
def _folder_files(sub_dirs, exts=(".pdf",)):
    out = []
    for sd in sub_dirs:
        d = path(INPUT_DIR, sd)
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.lower().endswith(exts):
                    with open(os.path.join(d, n), "rb") as f:
                        out.append((n, f.read()))
            if out:
                break
    return out


@st.cache_data(show_spinner=False, max_entries=64)
def _inspect(name, digest, tier, _data):
    return billing.inspect_file(name, _data, tier)


def inspect(name, data, tier):
    return _inspect(name, hashlib.sha1(data).hexdigest(), tier, data)


def _file_table(infos, role):
    rows = []
    for i in infos:
        dims = i.page_dims[0] if i.page_dims else None
        rows.append({"Role": role, "File": i.name, "Format": i.kind.upper(), "Size (KB)": round(i.size_bytes / 1024),
                     "Pages": i.pages, "Image size (px)": f"{dims[0]}×{dims[1]}" if dims else "—",
                     "Text characters": i.text_chars, "Est. input tokens": i.tokens, "Note": i.note})
    return rows


def grading_tab(cfg):
    ui.page_header("AI Grading", "Attendance sheets, quizzes and experiment handouts graded with Claude. Results "
                   "wait in Review & Publish until you approve them.")
    codes = cfgmod.all_courses(cfg)
    if not codes:
        st.info("Create a course on Courses & Criteria first.")
        return
    led = billing.load_ledger()
    s = led["settings"]
    bal1, bal2 = st.columns([4, 1.4])
    bal1.markdown(f'<div class="statusline"><span><b>Balance</b> ${led["balance_usd"]:.2f}</span>'
                  f'<span><b>Spent</b> ${led["total_spent_usd"]:.2f}</span>'
                  f'<span><b>Set</b> {(led.get("balance_set_at") or "never")[:10]}</span>'
                  f'<span><b>Warn at</b> {s["warn_pct_of_balance"]:g}% of balance</span>'
                  f'<span><b>Run caps</b> ${s["run_cost_cap_usd"]:.2f} · {int(s["run_token_cap"]):,} tokens</span></div>',
                  unsafe_allow_html=True)
    with bal2.popover("Update balance", use_container_width=True):
        amt = st.number_input("Amount (USD)", min_value=0.0, value=float(max(led["balance_usd"], 0.0)), step=1.0,
                              key="gr_bal_amt")
        note = st.text_input("Note", placeholder="e.g. Checked in the Anthropic Console", key="gr_bal_note")
        c_set, c_top = st.columns(2)
        if c_set.button("Set balance", key="gr_bal_set"):
            billing.save_ledger(billing.set_balance(led, amt, note))
            st.rerun()
        if c_top.button("Add as top-up", key="gr_bal_top"):
            billing.save_ledger(billing.top_up(led, amt, note))
            st.rerun()

    with st.container(border=True):
        terms = sorted({cfg["courses"][x].get("term") or "" for x in codes} - {""}) or [cfg.get("active_term", "")]
        a0, a1, a2, a3 = st.columns([1.1, 1.2, 1.6, 0.7])
        term = a0.selectbox("Term", terms, index=terms.index(cfg["active_term"]) if cfg["active_term"] in terms else 0,
                            key="gr_term")
        tcodes = [x for x in codes if (cfg["courses"][x].get("term") or "") == term] or codes
        code = a1.selectbox("Course", tcodes, key="gr_course")
        c = cfg["courses"][code]
        sec_opts = c["sections"] or ["901"]
        sections = a2.multiselect("Section(s) in this session", sec_opts, default=sec_opts[:1], key=f"gr_secs_{code}",
                                  help="Select more than one section when they took the assessment together.")
        week = a3.number_input("Week", min_value=1, max_value=max(20, c["weeks"]), value=1, step=1, key="gr_week")
        gradable = [x for x in c["components"] if x["type"] in ("quiz", "items", "single")]
        kinds = ["Attendance only"] + [x["label"] for x in gradable]
        b0, b1, b2, b3 = st.columns([1.3, 0.8, 2.2, 0.9])
        kind = b0.selectbox("Assessment", kinds, index=1 if len(kinds) > 1 else 0, key=f"gr_kind_{code}",
                            help="Every quiz/items/single criterion of the course can be graded from scanned papers.")
        comp = next((x for x in gradable if x["label"] == kind), None)
        target = "quiz" if (comp and comp["type"] == "quiz") else (comp["key"] if comp else "quiz")
        number = 0
        if comp is not None:
            if comp["type"] in ("quiz", "items"):
                number = b1.number_input("Item no.", min_value=1, max_value=comp["count"], value=1, step=1,
                                         key=f"gr_no_{code}_{target}")
            else:
                number = 1
                b1.text_input("Item no.", value="single", disabled=True, key=f"gr_single_{code}_{target}")
        models = list(billing.MODELS)
        model = b2.selectbox("Claude model", models, index=models.index(s["model"]) if s["model"] in models else 0,
                             format_func=lambda m: f"{billing.MODELS[m]['label']} · ${billing.MODELS[m]['input']:g} in / "
                                                   f"${billing.MODELS[m]['output']:g} out per MTok")
        ppp = b3.number_input("Pages per paper", min_value=1, max_value=12, value=1 if target == "quiz" else 2, step=1,
                              key=f"gr_ppp_{target}",
                              help="Experiment handouts, exams and reports usually have several pages per student.")
    if not sections:
        st.warning("Select at least one section.")
        return
    section = ",".join(sections)
    ec = comp if target != "quiz" else None
    tier = billing.MODELS[model]["tier"]
    paper_word = {"quiz": "quiz paper", "experiment": "experiment handout"}.get(target, f"{kind.lower()} paper")

    t_files, t_rubric = st.tabs(["Input files", "Rubric"])
    with t_files:
        src = st.radio("Source", ["Upload files", f"Use the {INPUT_DIR}/ folder"], horizontal=True,
                       label_visibility="collapsed")
        if src.startswith("Upload"):
            f1, f2, f3 = st.columns(3)
            att = [(f.name, f.getvalue()) for f in (f1.file_uploader("Attendance sheets (PDF)", type=["pdf"],
                                                                    accept_multiple_files=True, key="gr_att") or [])]
            papers = [(f.name, f.getvalue()) for f in (f2.file_uploader(f"Scanned {paper_word}s (PDF)", type=["pdf"],
                                                                       accept_multiple_files=True, key="gr_q") or [])] \
                if number else []
            qf = f3.file_uploader("Question sheet (optional)", type=["pdf", "png", "jpg", "jpeg"], key="gr_question",
                                  help="Used to generate a rubric when none is given, and archived with the papers.")
            question = (qf.name, qf.getvalue()) if qf else None
        else:
            att = _folder_files(["attendance"])
            sub = {"quiz": ["all_quizzes_scanned", "quizzes"], "experiment": ["experiments"]}.get(target, [target])
            papers = _folder_files(sub) if number else []
            qname = "quiz_question.pdf" if target == "quiz" else f"{target}_question.pdf"
            qp = path(INPUT_DIR, qname)
            question = (qname, open(qp, "rb").read()) if os.path.exists(qp) else None
            folder = f"input/{sub[0]}/"
            st.caption(f"input/attendance/: {len(att)} PDF(s) · {folder}: {len(papers)} PDF(s) · "
                       f"{qname}: {'found' if question else 'not found'}")
    rubric, save_rubric = "", False
    with t_rubric:
        if not number:
            st.caption("No rubric is needed for an attendance-only run.")
        else:
            default_rubric = open(path(RUBRIC_FILE), encoding="utf-8").read() if os.path.exists(path(RUBRIC_FILE)) else ""
            rf = st.file_uploader("Load a rubric text file (optional)", type=["txt", "md"], key="gr_rubric_file")
            if rf:
                default_rubric = rf.getvalue().decode("utf-8", "ignore")
            rubric = st.text_area("Rubric and official solution", default_rubric, height=180,
                                  key=f"gr_rubric_{hashlib.sha1(default_rubric.encode()).hexdigest()[:8]}")
            save_rubric = st.checkbox(f"Save as {RUBRIC_FILE}", value=False)

    if not att and not (number and papers):
        st.info(f"Load the attendance sheets{' and the ' + paper_word + 's' if number else ''} to see the estimate.")
        return

    # ---------------------------------------------------------------- pre-flight
    att_i = [inspect(n, d, tier) for n, d in att]
    q_i = [inspect(n, d, tier) for n, d in papers] if number else []
    qs_i = inspect(*question, tier) if (number and question and not rubric.strip()) else None
    roster = grader.load_roster(c)
    est = billing.estimate_run(model, att_i, q_i, len(rubric.strip()), len(roster), qs_i, s, ppp)
    st.markdown("##### Pre-flight estimate")
    ui.metric_grid([
        ui.metric_card("API calls", str(est.calls)),
        ui.metric_card("Input tokens", f"{est.input_tokens + est.cache_write_tokens + est.cache_read_tokens:,}"),
        ui.metric_card("Output tokens", f"{est.output_tokens:,}"),
        ui.metric_card("Projected cost", f"${est.cost_usd:.3f}", f"caching saves ${est.cache_savings_usd:.3f}"),
    ])
    with st.expander("Details: files and steps"):
        rows = _file_table(att_i, "Attendance") + _file_table(q_i, paper_word.capitalize() + "s") + \
            (_file_table([qs_i], "Question sheet") if qs_i else [])
        if rubric.strip():
            rows.append({"Role": "Rubric", "File": "rubric text", "Format": "TEXT",
                         "Size (KB)": round(len(rubric) / 1024, 1), "Pages": 1, "Image size (px)": "—",
                         "Text characters": len(rubric), "Est. input tokens": billing.text_tokens(rubric),
                         "Note": "Sent once, then read from cache"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.dataframe(pd.DataFrame(est.lines), use_container_width=True, hide_index=True)
        st.caption(f"Image tokens follow the {tier}-resolution tier of {billing.MODELS[model]['label']} "
                   f"(tokens = ⌈w/28⌉×⌈h/28⌉ after resizing). Output tokens are an average per call. "
                   f"Roster: {len(roster)} students across all sections.")

    warns = billing.threshold_warnings(est, led)
    errors = [m for lv, m in warns if lv == "error"]
    for lv, m in warns:
        {"error": st.error, "warning": st.warning}.get(lv, st.info)(m)
    api_key = ui.get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        st.error("ANTHROPIC_API_KEY is not set (environment variable or Streamlit secret). Grading cannot start.")
    ack = True
    if any(lv in ("error", "warning") for lv, _ in warns):
        ack = st.checkbox("I have read the warning(s) above and want to continue.")
    if errors and ack:
        st.caption("The run still stops automatically at the per-run cost/token cap.")
    if ec is not None and not os.path.exists(path(ec["workbook"])):
        st.warning(f"{ec['workbook']} does not exist yet. Create it on Courses & Criteria (Workbooks) before "
                   f"approving the results.")

    if st.button("Start grading", type="primary", disabled=not (api_key and ack)):
        if save_rubric and rubric.strip():
            from qs_core import backup as bk
            bk.backup_file(path(RUBRIC_FILE), course="GLOBAL")
            with open(path(RUBRIC_FILE), "w", encoding="utf-8") as f:
                f.write(rubric)
        job = grader.GradingJob(course=code, section=str(section), week=int(week), quiz=int(number) or None,
                                model=model, rubric=rubric, attendance_files=att,
                                quiz_files=papers if number else [], question_file=question if number else None,
                                pages_per_paper=int(ppp), target=target)
        run_live(job, api_key, est)


def run_live(job, api_key, est):
    st.markdown("##### Live progress")
    bar = st.progress(0.0, text="Starting...")
    k1, k2, k3, k4, k5 = st.columns(5)
    ph = [k.empty() for k in (k1, k2, k3, k4, k5)]
    log_box = st.empty()
    lines = []

    def on_event(ev):
        u = ev["usage"]
        frac = ev["done"] / ev["total"] if ev["total"] else 0
        stage = {"attendance": "Attendance", "paper": "Quiz papers", "rubric": "Rubric"}.get(ev["type"], ev["type"])
        bar.progress(min(frac, 1.0), text=f"{stage}: {ev['done']}/{ev['total']}")
        ph[0].metric("Processed", f"{ev['done']}/{ev['total']} {stage.lower()}")
        ph[1].metric("Input tokens", f"{u['input_tokens']:,}")
        ph[2].metric("Output tokens", f"{u['output_tokens']:,}")
        ph[3].metric("Cache read / write", f"{u['cache_read_tokens']:,} / {u['cache_write_tokens']:,}")
        ph[4].metric("Cost so far", f"${u['cost_usd']:.3f}", f"estimate ${est.cost_usd:.3f}", delta_color="off")
        lines.append(ev["message"])
        log_box.code("\n".join(lines[-10:]), language=None)

    try:
        q = grader.run_grading(job, api_key, on_event)
    except Exception as e:  # noqa: BLE001
        st.error(f"Grading failed: {e}")
        return
    bar.progress(1.0, text="Done")
    st.session_state["review_qid"] = q["id"]
    n_open = len(grader.open_flags(q))
    if q["status"] == "stopped":
        st.warning(" ".join(q["notes"]))
    st.success(f"Grading finished: {len(q['papers'])} paper(s), {len(q['attendance']['present'])} present, "
               f"cost ${q['usage'].get('cost_usd', 0):.3f}. {n_open} flag(s) need review. "
               f"Open **Review & Publish** to check and approve the results.")
    if q.get("archived_pdfs"):
        st.caption("PDFs saved to the term archive: " + ", ".join(q["archived_pdfs"]))


# ----------------------------------------------------------------------------- review
def _roster_label(roster, sid):
    r = roster.get(sid)
    return f"{sid} · {r['name']} · Sec {r['section']}" if r else f"{sid} · not on roster"


def review_tab(cfg):
    ui.page_header("Review & Publish", "Check flagged papers, fix IDs, scores and feedback, approve, then push.")
    ui.show_flash()
    queues = grader.list_queues()
    if not queues:
        st.info("No grading runs yet. Start one in the AI Grading tab.")
        return
    ids = [q["id"] for q in queues]
    default = st.session_state.get("review_qid")
    qid = st.selectbox("Grading run:", ids, index=ids.index(default) if default in ids else 0,
                       format_func=lambda i: grader.queue_label(next(q for q in queues if q["id"] == i)))
    st.session_state["review_qid"] = qid
    q = grader.load_queue(qid)
    c = cfgmod.course(cfg, q["course"])
    if c is None:
        st.error(f"Course {q['course']} is no longer configured.")
        return
    roster = grader.load_roster(c)
    open_ = grader.open_flags(q)
    fb_ok = grader.target_has_feedback(c, q.get("target", "quiz"))

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Status", q["status"].replace("_", " ").title())
    m2.metric("Papers", sum(1 for p in q["papers"] if not p.get("excluded")))
    m3.metric("Open flags", len(open_))
    m4.metric("Present", len(q["attendance"]["present"]) if q["attendance"].get("processed") else "—")
    m5.metric("Run cost", f"${q['usage'].get('cost_usd', 0):.3f}")
    for n in q.get("notes", []):
        st.warning(n)
    if q.get("archived_pdfs"):
        st.caption("Term archive: " + " · ".join(os.path.basename(p) for p in q["archived_pdfs"]))

    if q["status"] == "applied":
        applied_view(cfg, q)
        regrade_panel(q, roster)
        return
    editable = q["status"] in ("pending_review", "stopped")

    # ---------------------------------------------------------------- attendance
    if q["attendance"].get("processed"):
        with st.expander(f"Attendance ({len(q['attendance']['present'])} present)", expanded=bool(q["attendance"]["unknown"])):
            if q["attendance"]["unknown"]:
                st.warning("Some IDs on the attendance sheets were unclear or not on the roster:")
                st.dataframe(pd.DataFrame(q["attendance"]["unknown"])[["raw", "id", "how", "file", "page"]].rename(
                    columns={"raw": "Read as", "id": "Digits", "how": "Match", "file": "File", "page": "Page"}),
                    use_container_width=True, hide_index=True)
            opts = sorted(set(roster) | set(q["attendance"]["present"]))
            present = st.multiselect("Present students (add or remove):", opts, default=q["attendance"]["present"],
                                     format_func=lambda s: _roster_label(roster, s), disabled=not editable)
            if editable and st.button("Save attendance"):
                q["attendance"]["present"] = sorted(present)
                q["attendance"]["unknown"] = []
                grader.refresh_flags(q, roster)
                grader.save_queue(q)
                st.rerun()

    # ---------------------------------------------------------------- papers table
    if q["papers"]:
        st.markdown("##### Graded papers")
        rows = []
        for p in q["papers"]:
            fl = [f for f in p.get("flags", []) if f["severity"] in grader.BLOCKING]
            open_n = sum(1 for f in fl if not f.get("resolved"))
            rows.append({"Paper": p["pid"], "Pages": f"{p['file']} p.{p['page_from']}"
                         + (f"-{p['page_to']}" if p["page_to"] != p["page_from"] else ""),
                         "Read ID": p.get("raw_id", ""), "Student ID": p.get("student_id", ""),
                         "Name": roster.get(p.get("student_id"), {}).get("name", ""),
                         "Score": p.get("score"), "Feedback": p.get("feedback", ""),
                         "Flags": "; ".join(f["code"] for f in p.get("flags", [])) or "—",
                         "Open": open_n, "Resolved": bool(fl) and open_n == 0, "Exclude": bool(p.get("excluded"))})
        df = pd.DataFrame(rows)
        only = st.checkbox("Show only papers with open flags", value=bool(open_))
        view = df[df["Open"] > 0] if only else df
        if not fb_ok:
            st.caption(f"{grader.target_label(q.get('target', 'quiz'))} has no written feedback: only scores are "
                       f"stored.")
        edited = st.data_editor(
            view, use_container_width=True, hide_index=True, disabled=not editable, key=f"rev_editor_{qid}",
            column_config={"Score": st.column_config.NumberColumn(min_value=0, max_value=100, step=0.5),
                           "Feedback": st.column_config.TextColumn(width="large"),
                           "Resolved": st.column_config.CheckboxColumn(help="Mark every flag of this paper as resolved"),
                           "Exclude": st.column_config.CheckboxColumn(help="Ignore this paper (e.g. duplicate scan)")},
            column_order=["Paper", "Pages", "Read ID", "Student ID", "Name", "Score"]
            + (["Feedback"] if fb_ok else []) + ["Flags", "Open", "Resolved", "Exclude"])
        if editable and st.button("Save table edits"):
            _save_table(q, edited, roster)
            st.rerun()

        # ------------------------------------------------------------ flagged paper inspector
        flagged = [p for p in q["papers"] if any(f["severity"] in grader.BLOCKING for f in p.get("flags", []))]
        if flagged:
            st.markdown("##### Inspect a flagged paper")
            pid = st.selectbox("Paper:", [p["pid"] for p in flagged],
                               format_func=lambda i: next(f"{p['pid']} · ID {p.get('student_id') or '?'} · "
                                                          f"{sum(1 for f in p['flags'] if f['severity'] in grader.BLOCKING and not f.get('resolved'))} open"
                                                          for p in flagged if p["pid"] == i))
            p = next(x for x in q["papers"] if x["pid"] == pid)
            left, right = st.columns([1, 1])
            with left:
                if p.get("preview") and os.path.exists(path(p["preview"])):
                    st.image(path(p["preview"]), caption=f"{p['file']} page {p['page_from']}", use_container_width=True)
                else:
                    st.caption("No page preview is available for this paper.")
            with right:
                for f in p["flags"]:
                    label = {"error": "Error", "warning": "Check", "info": "Note"}[f["severity"]]
                    done = " (resolved)" if f.get("resolved") else ""
                    st.markdown(f"**{label}{done}:** {f['message']}")
                if p.get("breakdown"):
                    st.dataframe(pd.DataFrame(p["breakdown"]), hide_index=True, use_container_width=True)
                conf = [f"ID {p['id_confidence']}" if p.get("id_confidence") is not None else "",
                        f"score {p['score_confidence']}" if p.get("score_confidence") is not None else ""]
                st.caption("Model confidence: " + ", ".join(x for x in conf if x) if any(conf) else "")
                with st.form(f"insp_{qid}_{pid}"):
                    opts = [""] + sorted(roster)
                    cur = p.get("student_id") or ""
                    sid = st.selectbox("Student ID:", opts if cur in opts else opts + [cur],
                                       index=(opts if cur in opts else opts + [cur]).index(cur),
                                       format_func=lambda s: "— not assigned —" if not s else _roster_label(roster, s),
                                       disabled=not editable)
                    score = st.number_input("Score:", 0.0, 100.0, float(p["score"]) if p.get("score") is not None else 0.0,
                                            step=0.5, disabled=not editable)
                    fb = st.text_area("Feedback:", p.get("feedback", ""), disabled=not editable) if fb_ok else ""
                    resolve = st.checkbox("Mark this paper's flags as resolved", value=False, disabled=not editable)
                    exclude = st.checkbox("Exclude this paper", value=bool(p.get("excluded")), disabled=not editable)
                    if st.form_submit_button("Save paper", disabled=not editable):
                        changed = (sid != cur or abs(score - float(p.get("score") or 0)) > 1e-9
                                   or fb != p.get("feedback", ""))
                        p.update({"student_id": sid, "score": score, "feedback": fb, "excluded": exclude})
                        if changed:
                            p["edited"] = True
                            p.pop("error", None)
                            if sid != cur:
                                p["id_match"] = "manual"
                            if p.get("breakdown") and abs(sum(float(b.get("awarded") or 0) for b in p["breakdown"]) - score) > 0.5:
                                p["breakdown"] = []          # manual score replaces the model breakdown
                        grader.refresh_flags(q, roster)
                        if resolve:
                            for f in p["flags"]:
                                f["resolved"] = True
                        grader.save_queue(q)
                        st.rerun()

    regrade_panel(q, roster)

    # ---------------------------------------------------------------- approval
    st.divider()
    absent_table(grader.absent_preview(q) if editable else [], before=True)
    open_ = grader.open_flags(q)
    if open_:
        st.warning(f"{len(open_)} flag(s) are still open. Fix or resolve every flagged paper before writing to Excel.")
    ok = st.checkbox("I reviewed the results and approve writing them to the course workbooks and the portal database.",
                     disabled=bool(open_) or not editable)
    if st.button("Approve & write to Excel", type="primary", disabled=not ok or bool(open_) or not editable):
        try:
            with st.spinner("Writing results (stamped backups first)..."):
                grader.apply_queue(q)
            ui.refresh()
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Writing failed: {e}")


def _save_table(q, edited: pd.DataFrame, roster):
    by = {p["pid"]: p for p in q["papers"]}
    resolve_pids = []
    for _, r in edited.iterrows():
        p = by.get(r["Paper"])
        if not p:
            continue
        sid = clean_id(r["Student ID"])
        score = None if pd.isna(r["Score"]) else float(r["Score"])
        fb = "" if ("Feedback" not in r or pd.isna(r["Feedback"])) else str(r["Feedback"])
        if sid != (p.get("student_id") or "") or score != (None if p.get("score") is None else float(p["score"])) \
                or fb != (p.get("feedback") or ""):
            p["edited"] = True
            p.pop("error", None)
            if sid != (p.get("student_id") or ""):
                p["id_match"] = "manual"
            if p.get("breakdown") and score is not None and \
                    abs(sum(float(b.get("awarded") or 0) for b in p["breakdown"]) - score) > 0.5:
                p["breakdown"] = []
        p.update({"student_id": sid, "score": score, "feedback": fb, "excluded": bool(r["Exclude"])})
        if bool(r["Resolved"]):
            resolve_pids.append(p["pid"])
    grader.refresh_flags(q, roster)
    for pid in resolve_pids:
        for f in by[pid]["flags"]:
            f["resolved"] = True
    grader.save_queue(q)


def regrade_panel(q, roster):
    """Re-grade queue: re-run Claude only for selected papers, with an optional focus and instruction."""
    papers = [p for p in q["papers"] if not p.get("excluded")]
    if not papers:
        return
    flagged = {p["pid"] for p in papers if any(f["severity"] in grader.BLOCKING and not f.get("resolved")
                                               for f in p.get("flags", []))}
    n_re = sum(1 for p in papers if p.get("regraded"))
    with st.expander(f"Re-grade selected papers{f' · {n_re} re-graded so far' if n_re else ''}"):
        st.caption("Runs Claude again only for the papers you pick. The previous result is kept in each paper's "
                   "history; the new score and feedback wait here for approval like any other result.")

        def label(pid):
            p = next(x for x in papers if x["pid"] == pid)
            name = roster.get(p.get("student_id"), {}).get("name", "")
            return (f"{pid} · {p.get('student_id') or 'no ID'}{' · ' + name if name else ''} · score "
                    f"{p.get('score') if p.get('score') is not None else '—'}{' · flagged' if pid in flagged else ''}"
                    f"{' · re-graded' if p.get('regraded') else ''}")
        r1, r2 = st.columns([3, 2])
        pick = r1.multiselect("Papers", [p["pid"] for p in papers], default=sorted(flagged)[:0],
                              format_func=label, key=f"rg_pick_{q['id']}")
        focus = r2.text_input("Question / part to focus on (optional)", placeholder="e.g. Part C, moment about O",
                              key=f"rg_focus_{q['id']}")
        note = st.text_area("Extra instruction for Claude (optional)", height=70, key=f"rg_note_{q['id']}",
                            placeholder="e.g. The student used a different but valid sign convention; apply error "
                                        "carried forward.")
        stored = q.get("rubric") or q.get("generated_rubric") or ""
        if not stored:
            default = open(path(RUBRIC_FILE), encoding="utf-8").read() if os.path.exists(path(RUBRIC_FILE)) else ""
            rubric = st.text_area("Rubric (this run has no stored rubric)", default, height=140,
                                  key=f"rg_rub_{q['id']}")
        else:
            rubric = stored
            st.caption("Uses the rubric stored with this run.")
        models = list(billing.MODELS)
        m_default = q.get("model") if q.get("model") in models else billing.load_ledger()["settings"]["model"]
        c1, c2 = st.columns([2, 3])
        model = c1.selectbox("Model", models, index=models.index(m_default) if m_default in models else 0,
                             format_func=lambda m: billing.MODELS[m]["label"], key=f"rg_model_{q['id']}")
        if pick:
            tier = billing.MODELS[model]["tier"]
            per = []
            for pid in pick:
                p = next(x for x in papers if x["pid"] == pid)
                b64 = grader._paper_pdf_b64(q, p)
                if b64:
                    import base64
                    per.append(billing.inspect_file(f"{pid}.pdf", base64.b64decode(b64), tier))
            est = billing.estimate_run(model, [], per, len(rubric), len(roster), None,
                                       billing.load_ledger()["settings"], 1)
            missing = len(pick) - len(per)
            c2.markdown(f'<div class="statusline"><span><b>Estimate</b> ${est.cost_usd:.3f}</span>'
                        f'<span><b>Tokens</b> {est.total_tokens:,}</span><span><b>Calls</b> {est.calls}</span>'
                        f'{"<span><b>Missing pages</b> " + str(missing) + "</span>" if missing else ""}</div>',
                        unsafe_allow_html=True)
        api_key = ui.get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            st.caption("Set ANTHROPIC_API_KEY to re-grade.")
        if st.button(f"Re-grade {len(pick)} paper(s)", disabled=not (pick and api_key and rubric.strip()),
                     key=f"rg_go_{q['id']}"):
            bar = st.progress(0.0, text="Re-grading...")
            k1, k2, k3 = st.columns(3)
            ph = [k.empty() for k in (k1, k2, k3)]
            box = st.empty()
            lines = []

            def on_event(ev):
                u = ev["usage"]
                bar.progress(min(ev["done"] / ev["total"], 1.0), text=f"Re-grading {ev['done']}/{ev['total']}")
                ph[0].metric("Papers", f"{ev['done']}/{ev['total']}")
                ph[1].metric("Tokens (in / out)", f"{u['input_tokens'] + u['cache_read_tokens']:,} / "
                                                  f"{u['output_tokens']:,}")
                ph[2].metric("Cost so far", f"${u['cost_usd']:.3f}")
                lines.append(ev["message"])
                box.code("\n".join(lines[-8:]), language=None)
            try:
                res = grader.regrade(q, pick, api_key, note=note, question=focus,
                                     rubric=None if stored else rubric, model=model, on_event=on_event)
                msg = f"Re-graded {len(res['done'])} paper(s) for ${res['usage']['cost_usd']:.3f}."
                if res["errors"]:
                    msg += " Problems: " + " ".join(res["errors"])
                ui.flash("warning" if res["errors"] else "success", msg + " Review and approve the new results.")
            except Exception as e:  # noqa: BLE001
                ui.flash("error", f"Re-grade failed: {e}")
            st.rerun()
        hist = [(p["pid"], h) for p in papers for h in p.get("history", [])]
        if hist:
            st.dataframe(pd.DataFrame([{"Paper": pid, "Replaced at": h["at"][:16].replace("T", " "),
                                        "Previous score": h.get("score"), "Previous ID": h.get("student_id"),
                                        "Current score": next(p for p in papers if p["pid"] == pid).get("score")}
                                       for pid, h in hist]), use_container_width=True, hide_index=True)


def applied_view(cfg, q):
    s = q.get("apply_summary", {})
    st.success(f"Written on {q.get('applied_at', '')[:16].replace('T', ' ')}: {s.get('students', 0)} students "
               f"({s.get('present', 0)} present), portal records {s.get('db', {}).get('updated', 0)} updated / "
               f"{s.get('db', {}).get('added', 0)} added.")
    if s.get("visitors"):
        st.caption(f"{len(s['visitors'])} student(s) from other sections attended this session; their records were "
                   f"written to their own sections.")
    st.markdown("##### Pre-push summary")
    files = [DB_FILE, META_FILE] + list(s.get("publish", []))
    local = [f for f in s.get("files", {}) if f not in files]
    if local:
        st.caption("Updated locally (not published, git-ignored): " + ", ".join(
            f"{f} ({v.get('cells')} cell(s))" for f, v in s["files"].items() if f in local))
    kind = grader.target_label(q.get("target", "quiz"))
    show_pre_push(files, f"Grades: {q['course']} Sec {q['section']} Week {q['week']}"
                         + (f" {kind} {q['quiz']}" if q.get("quiz") else " attendance"), key=q["id"],
                  auto_zero=s.get("absent_zeros") or [])


def absent_table(rows, before: bool = False):
    """Absent students who receive (or received) 0 automatically."""
    if not rows:
        if before:
            st.caption("No absent student will receive an automatic 0 in this run.")
        return
    item = rows[0].get("Item", "")
    head = (f"{len(rows)} absent student(s) will receive 0 for {item} when you approve"
            if before else f"Auto-zeroed absent students ({len(rows)})")
    with st.expander(head, expanded=before):
        if before:
            st.caption("Marked absent for this week and without a score. A score that is already entered "
                       "(e.g. a make-up) is kept. Feedback: \"Absent - 0 points.\" (quizzes and experiments only).")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def show_pre_push(files, message, key="pp", auto_zero=None):
    """Pre-Push Quick Preview: changed files and the courses / weeks / sections / items they affect.
    Nothing is committed or pushed until the instructor ticks the confirmation and presses the button."""
    if not gitops.repo_available():
        files = [f for f in files if f in ui.pending_files()]    # no git clone: only files saved this session
    summary = gitops.pre_push_summary(files) if files else []
    if auto_zero:
        absent_table(auto_zero)               # absent students that received 0 automatically in this change
    if not summary:
        st.info("Nothing to publish: these files have no unpublished changes: " + ", ".join(files))
        _other_changes(files)
        return
    courses = sorted({c for i in summary for c in i["courses"]})
    weeks = gitops._sort_weeks({w for i in summary for w in i["weeks"]})
    sections = sorted({x for i in summary for x in i["sections"]}, key=lambda x: (len(x), x))
    st.markdown("##### Pre-push quick preview")
    ui.metric_grid([
        ui.metric_card("Files to push", str(len(summary))),
        ui.metric_card("Courses", ", ".join(courses) or "—"),
        ui.metric_card("Weeks", ", ".join(w.replace("Week ", "W") for w in weeks) or "—"),
        ui.metric_card("Sections", ", ".join(sections) or "—"),
    ])
    st.dataframe(pd.DataFrame([{
        "File": i["file"], "Change": i["status"], "Course(s)": ", ".join(i["courses"]) or "—",
        "Week(s)": ", ".join(i["weeks"]) or "—", "Section(s)": ", ".join(i["sections"]) or "—",
        "Records / items": ", ".join(i["items"][:6]) + (" …" if len(i["items"]) > 6 else "") or "—",
        "Changed records": i["records"] or "—"} for i in summary]), use_container_width=True, hide_index=True)
    with st.expander("Details per file"):
        for item in summary:
            st.markdown(f"**{item['file']}** · {item['status']}")
            st.code("\n".join(item["details"]), language=None)
    _other_changes(files)
    msg = st.text_input("Commit message", message, key=f"msg_{key}")
    ok = st.checkbox("I reviewed this preview. Commit these files and push them to GitHub.", key=f"ok_{key}")
    if st.button("Commit & push", type="primary", key=f"push_{key}", disabled=not ok) and ok:
        ui.report_publish(ui.publish_files([i["file"] for i in summary], msg))
        st.session_state.pop(f"ok_{key}", None)
        st.rerun()
    if not ok:
        st.caption("Nothing is pushed until you tick the confirmation above.")


def _other_changes(files):
    """Local changes that are NOT part of this push (git status), shown for awareness only."""
    if not gitops.repo_available():
        return
    others = [(st_, f) for st_, f in gitops.all_local_changes() if f not in files]
    if others:
        with st.expander(f"Other local changes not included in this push ({len(others)})"):
            st.dataframe(pd.DataFrame([{"Status": {"M": "modified", "??": "untracked", "D": "deleted"}.get(a, a),
                                        "File": f} for a, f in others]), use_container_width=True, hide_index=True)
