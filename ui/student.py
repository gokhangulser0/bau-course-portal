"""Student portal: announcement feed, New badges, overview/standing, What-If, weekly log, class comparison."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import portal_logic as pl
from portal_logic import fmt_num, safe_html as esc
from qs_core import announcements as annmod
from qs_core import badges
from qs_core import config as cfgmod
from qs_core import standing as stmod
from qs_core.common import clean_id, clean_section

from . import common as ui


# ----------------------------------------------------------------------------- charts
def trend_chart(frame):
    return (
        alt.Chart(frame).mark_line(point=True)
        .encode(
            x=alt.X("quiz_n:O", title="Quiz"),
            y=alt.Y("score:Q", title="Score", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("series:N", title=None),
            tooltip=[alt.Tooltip("quiz_n:O", title="Quiz"), alt.Tooltip("series:N", title=""),
                     alt.Tooltip("score:Q", title="Score")],
        ).properties(height=260)
    )


def hist_chart(hist):
    return (
        alt.Chart(hist).mark_bar()
        .encode(
            x=alt.X("bin:N", sort=list(hist["bin"]), title="Score range"),
            y=alt.Y("count:Q", title="Students"),
            color=alt.condition("datum.mine", alt.value("#ff7f0e"), alt.value("#4c78a8")),
            tooltip=[alt.Tooltip("bin:N", title="Range"), alt.Tooltip("count:Q", title="Students")],
        ).properties(height=240)
    )


# ----------------------------------------------------------------------------- feed
def feed_html(items) -> str:
    if not items:
        return ""
    parts = []
    for a in items:
        new = badges.announcement_recent(a)
        when = (a.get("updated_at") or a.get("created_at") or "")[:16].replace("T", " ")
        parts.append(
            f'<div class="feed-item"><div class="t">{esc(a["title"])}{ui.NEW_BADGE if new else ""}</div>'
            f'<div class="m">{esc(annmod.scope_label(a))} · {esc(when)}</div>'
            f'<div class="b">{esc(a["body"])}</div></div>')
    return '<div class="feed">' + "".join(parts) + "</div>"


def show_feed(items, title="Announcements"):
    if items:
        st.markdown(f"#### {title}")
        st.markdown(feed_html(items), unsafe_allow_html=True)


# ----------------------------------------------------------------------------- per-course data
def course_rows(df, sid, course):
    if df.empty:
        return pd.DataFrame()
    return df[(df["student_id"] == sid) & (df["course_code"] == course)]


def find_enrolments(cfg, df_all, sid):
    """[(course, section, name)] from the portal database and every course workbook."""
    found = {}
    if not df_all.empty:
        m = df_all[df_all["student_id"] == sid]
        for course, g in m.groupby("course_code"):
            found[course] = [pl.student_section(g), g.iloc[0]["full_name"]]
    ros = ui.roster_sheet()
    if not ros.empty:
        for r in ros[ros["student_id"] == sid].to_dict("records"):
            cur = found.setdefault(r["course_code"], ["", ""])
            cur[0] = cur[0] or r["section"]
            cur[1] = cur[1] or r["full_name"]
    for code, c in cfg["courses"].items():
        for comp in cfgmod.workbook_components(c):
            row = stmod.student_row(ui.table(comp["workbook"]), sid)
            if row:
                cur = found.setdefault(code, ["", ""])
                cur[0] = cur[0] or clean_section(row.get("Section"))
                cur[1] = cur[1] or str(row.get("Full Name") or "")
    return [(c, s, n) for c, (s, n) in sorted(found.items())]


def record_items(course_code, c, sid, rows, status_rows, tables, updates):
    """[(label, group)] for every record of this student updated within the last badges.NEW_DAYS days."""
    out = []
    if not rows.empty:
        # A row with a quiz score reports the quiz; only attendance-only rows report attendance
        att_only = rows[rows["quiz_score_num"].isna()] if "quiz_score_num" in rows else rows
        for wk, g in att_only.groupby("week_no"):
            if any(badges.is_recent(v) for v in g.get("updated_at", [])):
                out.append((f"{course_code} · Week {int(wk)} attendance", f"week:{int(wk)}"))
        for _, r in rows[rows["quiz_n"].notna() & rows["quiz_score_num"].notna()].iterrows():
            if badges.is_recent(r.get("updated_at")):
                q = int(r["quiz_n"])
                out.append((f"{course_code} · Quiz {q} score and feedback", f"quiz:{q}"))
    for rec in (status_rows.to_dict("records") if not status_rows.empty else []):
        if badges.is_recent(rec.get("created_at")):
            w = int(rec["week_no"])
            out.append((f"{course_code} · Week {w}: no class", f"week:{w}"))
    if c:
        for comp in cfgmod.workbook_components(c):
            row = stmod.student_row(tables.get(comp["workbook"]), sid) or {}
            for col in cfgmod.item_columns(comp):
                if stmod.to_number(row.get(col)) is None:
                    continue
                ts = badges.item_updated(updates, course_code, col, sid)
                if ts and badges.is_recent(ts):
                    out.append((f"{course_code} · {col}", f"item:{comp['key']}:{col}"))
    return out


# ----------------------------------------------------------------------------- cards
def class_week_card(item, section, stats, course, open_feedback, new_groups):
    chip = ('<span class="chip chip-present">Present</span>' if item["attendance"] == "+"
            else '<span class="chip chip-absent">Absent</span>')
    attended = item["attended_section"]
    visit = ""
    if item["attendance"] == "+" and attended and section and attended != section:
        visit = f'<span class="chip chip-visit">Attended the Section {esc(attended)} session</span>'
    wk_new = f"week:{item['week_no']}" in new_groups
    n_q = len(item["quizzes"])
    nq = f' <span class="muted">· {n_q} quizzes</span>' if n_q > 1 else ""
    quiz_html, can_open = [], open_feedback
    for r in item["quizzes"]:
        q = int(r["quiz_n"])
        bits = [f'<span class="quiz-name">Quiz {q}</span>',
                f'<span class="score">{esc(fmt_num(r["quiz_score_num"]))} / 100</span>']
        if f"quiz:{q}" in new_groups:
            bits.append(ui.NEW_BADGE)
        st_ = stats.get((course, q))
        if st_:
            bits.append(f'<span class="muted">Class average: <b>{st_["mean"]:.1f}</b> / 100</span>')
        fb = r.get("feedback")
        fb_html = ""
        if pd.notna(fb) and str(fb).strip():
            op = " open" if can_open else ""
            can_open = False
            fb_html = (f'<details{op}><summary>Instructor feedback</summary>'
                       f'<div class="fb">{esc(fb)}</div></details>')
        quiz_html.append(f'<div class="quiz-row"><div class="row">{"".join(bits)}</div>{fb_html}</div>')
    if not quiz_html:
        quiz_html = ['<div class="row"><span class="muted">No quiz evaluated this week.</span></div>']
    return (f'<div class="week-card"><div class="head"><span>{esc(item["label"])}{nq}'
            f'{ui.NEW_BADGE if wk_new else ""}</span>'
            f'<span>{chip} {visit}</span></div>{"".join(quiz_html)}</div>')


def no_class_card(item, new_groups):
    note = str(item["row"].get("note") or "").strip()
    note_html = f'<div class="note"><b>Instructor note:</b> {esc(note)}</div>' if note else ""
    badge = ui.NEW_BADGE if f"week:{item['week_no']}" in new_groups else ""
    return (f'<div class="week-card"><div class="head"><span>{esc(item["label"])}{badge}</span>'
            f'<span class="chip chip-noclass">No class this week</span></div>'
            f'<div class="muted" style="margin-top:6px">This week does not count as an absence.</div>'
            f'{note_html}</div>')


# ----------------------------------------------------------------------------- overview


def _ordered(results):
    rank = {n: i for i, n in enumerate(cfgmod.CRITERIA_ORDER)}
    return sorted(results, key=lambda r: rank.get(cfgmod.criterion_of(r.comp) or "", 99))


def _is_new(r, new_groups) -> bool:
    comp = r.comp
    if comp["type"] == "quiz":
        return any(f"quiz:{i.label.split()[-1]}" in new_groups for i in r.items)
    return any(f"item:{comp['key']}:{i.label}" in new_groups for i in r.items)


def criterion_card(r, att, new_groups, show_points: bool) -> str:
    """One evaluation-criterion card: name, weight, score/status, contribution bar, item chips."""
    comp = r.comp
    name = comp["label"]
    w = comp["weight"]
    weight_html = f'<span class="crit-weight">{w:g}%</span>' if w > 0 else \
        '<span class="crit-weight muted">Weight not set</span>'
    n_items = len(r.items)
    n_eval = len(r.evaluated)
    n_held = sum(1 for i in r.items if i.held)
    if comp["type"] == "attendance":
        s = r.items[0].score
        value = f"{s:.0f}%" if s is not None else "—"
        status = ("graded", f"{att[0]} of {att[1]} weeks attended") if s is not None else ("pending", "No weeks recorded yet")
    elif n_items == 1:
        s = r.items[0].score
        value = f"{fmt_num(s)}<small> / 100</small>" if s is not None else "—"
        if s is not None:
            status = ("graded", "Graded")
        elif r.items[0].held:
            status = ("missing", "No score recorded for you")
        else:
            status = ("pending", "Not published yet")
    else:
        value = f"{r.display_avg:.1f}<small> / 100</small>" if r.display_avg is not None else "—"
        if n_held == 0:
            status = ("pending", "Not published yet")
        elif n_held >= n_items:
            status = ("graded", f"Complete · {n_eval} of {n_items} graded")
        else:
            status = ("progress", f"In progress · {n_held} of {n_items} held")
    rule = stmod.rule_text(comp) if comp["type"] in ("quiz", "items") else ""
    bar = ""
    if w > 0 and show_points:
        pct = max(0.0, min(100.0, 100.0 * r.earned / w)) if w else 0
        bar = (f'<div class="crit-bar"><div style="width:{pct:.1f}%"></div></div>'
               f'<div class="crit-pts">+{r.earned:.2f} of {w:.2f} pts</div>')
    chips = ""
    if n_items > 1 and comp["type"] != "quiz":
        parts = []
        for i in r.items:
            cls = "chip-done" if i.score is not None else ("chip-miss" if i.held else "chip-wait")
            txt = fmt_num(i.score) if i.score is not None else ("0" if i.held else "—")
            nb = " •" if f"item:{comp['key']}:{i.label}" in new_groups else ""
            parts.append(f'<span class="item-chip {cls}" title="{esc(i.label)}">{esc(i.label)}: {txt}{nb}</span>')
        chips = '<div class="crit-chips">' + "".join(parts) + "</div>"
    badge = ui.NEW_BADGE if _is_new(r, new_groups) else ""
    return (f'<div class="crit-card"><div class="crit-head"><span class="crit-name">{esc(name)}{badge}</span>'
            f'{weight_html}</div>'
            f'<div class="crit-value">{value}</div>'
            f'<div class="crit-status st-{status[0]}">{esc(status[1])}</div>'
            f'{f"<div class=crit-rule>{esc(rule.capitalize())}</div>" if rule else ""}'
            f'{bar}{chips}</div>')


def overview_tab(cfg, c, course, sid, section, rows, df_db, stats, tables, new_groups):
    qscores = pl.student_quiz_scores(rows)
    results = None
    if c and c["components"]:
        held = pl.held_quizzes(df_db, course, section) | set(qscores)
        att = pl.student_attendance(rows)
        results = _ordered(stmod.compute(c, sid, qscores, held, tables, attendance=att))
        show = bool(c.get("show_standing"))
        earned, base = stmod.totals(results)
        total_w = sum(r.weight for r in results if r.weight > 0)

        st.markdown("#### Evaluation Criteria")
        st.caption("Every assessment of this course, its weight in the final grade and your current result.")
        st.markdown('<div class="crit-grid">' + "".join(criterion_card(r, att, new_groups, show) for r in results)
                    + "</div>", unsafe_allow_html=True)
        for r in results:
            comp = r.comp
            if comp["type"] not in ("items", "single") or not cfgmod.has_feedback(comp):
                continue                      # feedback only for Experiment / Lab Report (and quizzes above)
            row = stmod.student_row(tables.get(comp["workbook"]), sid) or {}
            notes = [(i.label, i.score, row.get(cfgmod.feedback_column(i.label))) for i in r.items]
            notes = [(lb, sc, fb) for lb, sc, fb in notes if sc is not None or (fb and str(fb).strip()
                                                                               and str(fb).lower() != "nan")]
            if notes:
                with st.expander(f"{comp['label']} feedback ({len(notes)})"):
                    for lb, sc, fb in notes:
                        score = f"{fmt_num(sc)} / 100" if sc is not None else "No score"
                        text = esc(fb) if fb and str(fb).lower() != "nan" else "No written feedback."
                        st.markdown(f'<div class="fb"><b>{esc(lb)}</b> · {score}<br>{text}</div>',
                                    unsafe_allow_html=True)
        if show:
            ui.metric_grid([
                ui.metric_card("Current standing", f"{earned:.2f} / {base:.2f} pts",
                               f"From criteria that have started ({base:g}% of the grade)"),
                ui.metric_card("Weighted so far", f"{(100 * earned / base) if base else 0:.1f}%",
                               "Your average over the graded part"),
                ui.metric_card("Total configured weight", f"{total_w:g}%",
                               "Remaining criteria are still to come" if base < total_w else "All criteria have started"),
            ])
        else:
            st.info("Your instructor has not published the overall standing for this course yet. The scores above "
                    "are shown as they are entered.")
    elif c:
        st.info("No evaluation criteria have been published for this course yet.")

    trend = pl.trend_frame(rows, stats, course) if not rows.empty else pd.DataFrame()
    if not trend.empty:
        st.markdown("#### Your Quiz Scores vs. Class Average")
        st.altair_chart(trend_chart(trend), use_container_width=True)

    if results and c.get("show_standing"):
        whatif_block(results, qscores, sid, course)


def whatif_block(results, qscores, sid, course):
    pending = [r for r in results if r.weight > 0 and r.pending_count > 0 and r.comp["type"] != "attendance"]
    st.markdown("#### What-if Calculator")
    if not pending:
        st.info("Every graded component has been held, so there is nothing left to simulate.")
        return
    assumed = {}
    cols = st.columns(min(3, len(pending)))
    for k, r in enumerate(pending):
        comp = r.comp
        ev = r.evaluated
        default = int(pl.half_up(sum(ev) / len(ev))) if ev else 70
        what = (f"the {r.pending_count} remaining {comp['label'].lower()} item(s)" if comp["type"] != "single"
                else f"the {comp['label'].lower()}")
        with cols[k % len(cols)]:
            assumed[r.key] = st.slider(f"If I score on average ... on {what}:", 0, 100, default,
                                       key=f"whatif_{sid}_{course}_{r.key}")
    res = stmod.what_if(results, assumed)
    cards = []
    for r in pending:
        cur, proj, _mx = res["per"][r.key]
        cards.append(ui.metric_card(f"{r.comp['label']} points", f"{proj:.2f} / {r.weight:.2f}",
                                    f"{proj - cur:+.2f} vs. now"))
    cards.append(ui.metric_card(f"Projected standing ({res['base']:g}% base)",
                                f"{res['projected']:.2f} / {res['base']:.2f}",
                                f"max possible {res['max']:.2f}"))
    ui.metric_grid(cards)
    notes = [f"{r.comp['label']}: {stmod.rule_text(r.comp)}" for r in pending if r.comp["type"] != "single"]
    st.caption(("; ".join(notes) + ". " if notes else "") +
               "A cancelled class does not change the total number of quizzes. "
               "This is an estimate, not an official grade.")


# ----------------------------------------------------------------------------- main view
def render():
    cfg = ui.config()
    settings = cfg["settings"]
    df_all, df_db, status_df, stats, _hidden = ui.database()
    all_ann = ui.announcements()
    limit = int(settings.get("announcement_feed_size", 5))

    ui.page_header("Student Academic Portal", "Enter your Student ID to see your attendance, quiz results, "
                   "every evaluation criterion of your courses and your weighted standing.")
    ui.last_updated_caption()

    raw = st.text_input("Student ID:", placeholder="e.g. 1111111")
    sid = clean_id(raw)
    if not sid:
        show_feed(annmod.relevant(all_ann, None, limit))
        return

    enrol = find_enrolments(cfg, df_all, sid)
    if not enrol:
        st.error("No record found matching this Student ID. Please make sure your ID is entered correctly.")
        show_feed(annmod.relevant(all_ann, None, limit))
        return

    name = next((n for _, _, n in enrol if n), sid)
    updates = badges.load_updates()

    # Records updated within the last NEW_DAYS days get a "New" badge (no browser cookies needed)
    feed = annmod.relevant(all_ann, [(c, s) for c, s, _ in enrol], limit)
    new_labels, new_groups_by_course = [], {}
    for code, sec, _ in enrol:
        c = cfgmod.course(cfg, code)
        rows = course_rows(df_db, sid, code)
        srows = status_df[(status_df["course_code"] == code) & (status_df["section"] == sec)] if sec \
            else pl.empty_status()
        tables = ui.course_tables(c) if c else {}
        items_ = record_items(code, c, sid, rows, srows, tables, updates)
        new_labels += [label for label, _ in items_]
        new_groups_by_course[code] = {group for _, group in items_}

    st.success(f"Welcome, **{name}**!")
    show_feed(feed, "Announcements for you")

    if new_labels:
        lis = "".join(f"<li>{esc(x)}</li>" for x in new_labels[:12])
        more = f"<li>… and {len(new_labels) - 12} more</li>" if len(new_labels) > 12 else ""
        st.markdown(f'<div class="whatsnew"><b>Updated in the last {badges.NEW_DAYS} days</b><ul>{lis}{more}</ul>'
                    f'</div>', unsafe_allow_html=True)

    codes = [c for c, _, _ in enrol]
    course = st.selectbox("Select Enrolled Course:", codes,
                          format_func=lambda x: f"{x} (updated)" if new_groups_by_course.get(x) else x)
    c = cfgmod.course(cfg, course)
    section = next((s for cc, s, _ in enrol if cc == course), "")
    title = f" — {c['title']}" if c and c.get("title") else ""
    st.markdown(f"### Academic Status: `{course}`{title}")
    rows = course_rows(df_db, sid, course)
    status_rows = status_df[(status_df["course_code"] == course) & (status_df["section"] == section)] \
        if section else pl.empty_status()
    tables = ui.course_tables(c) if c else {}
    new_groups = new_groups_by_course.get(course, set())
    qscores = pl.student_quiz_scores(rows)

    tab_over, tab_log, tab_class = st.tabs(["Overview", "Weekly Log", "Class Comparison"])
    with tab_over:
        overview_tab(cfg, c, course, sid, section, rows, df_db, stats, tables, new_groups)

    with tab_log:
        st.markdown("#### Weekly Attendance & Quiz Log")
        timeline = pl.build_timeline(rows, status_rows, newest_first=settings.get("newest_first", True))
        if not timeline:
            st.info(f"No weekly attendance or quiz records published for `{course}` yet.")
        else:
            opened, parts = False, []
            for item in timeline:
                if item["type"] == "no_class":
                    parts.append(no_class_card(item, new_groups))
                else:
                    has_fb = any(pd.notna(r.get("feedback")) and str(r.get("feedback")).strip() != ""
                                 for r in item["quizzes"])
                    parts.append(class_week_card(item, section, stats, course, has_fb and not opened, new_groups))
                    opened = opened or has_fb
            st.markdown("".join(parts), unsafe_allow_html=True)

    with tab_class:
        st.markdown("#### How did the class do?")
        min_n = int(settings.get("min_class_for_chart", pl.MIN_CLASS_FOR_CHART))
        quizzes = sorted(q for (cc, q), s in stats.items() if cc == course and s["n"] >= min_n)
        if not quizzes:
            st.info("The class distribution will appear here once enough quizzes have been evaluated.")
        else:
            def _qlabel(q):
                w = stats[(course, q)]["week_no"]
                return f"Quiz {q}" + (f" (Week {w})" if w else "")
            q_sel = st.selectbox("Select quiz:", quizzes, index=len(quizzes) - 1, format_func=_qlabel,
                                 key=f"dist_quiz_{sid}_{course}")
            s = stats[(course, q_sel)]
            mine = qscores.get(q_sel)
            hist = pl.histogram(s["scores"])
            hist["mine"] = hist["start"].eq(pl.bin_index(mine) * 10) if mine is not None else False
            ui.metric_grid([
                ui.metric_card("Your score", f"{fmt_num(mine)} / 100" if mine is not None else "N/A"),
                ui.metric_card("Class average", f"{s['mean']:.1f}"),
                ui.metric_card("Class median", fmt_num(s["median"])),
                ui.metric_card("Students evaluated", str(s["n"])),
            ])
            st.altair_chart(hist_chart(hist), use_container_width=True)
            if mine is not None:
                st.caption("The orange bar is the score range you are in.")
