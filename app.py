import os

import altair as alt
import pandas as pd
import streamlit as st

import portal_logic as pl
from portal_logic import clean_id_str, fmt_num, safe_html as esc

# --- Sayfa Yapılandırması ---
st.set_page_config(
    page_title="Academic Portal - BAU Mechatronics",
    page_icon="🎓",
    layout="wide"
)

# --- Dosyalar ve ayarlar ---
DB_FILE = "grades_database.xlsx"
HW_FILE = "MCH2008_homework.xlsx"
STATUS_FILE = "class_status.csv"      # "No Class This Week" kayıtları
META_FILE = "portal_meta.json"        # process_week.py'nin yazdığı "son güncelleme" bilgisi
NEWEST_FIRST = True                   # Haftalık kayıtlarda en yeni hafta üstte


def get_secret(name, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


# Öneri: şifreyi koda yazma. Streamlit Cloud > Settings > Secrets içine INSTRUCTOR_PASSWORD ekle,
# sonra aşağıdaki "or ..." kısmını sil.
INSTRUCTOR_PASSWORD = get_secret("INSTRUCTOR_PASSWORD") or "a123b456c789++"

# --- Özel CSS: açık/koyu temada çalışır, telefonda sıkışmaz ---
st.markdown("""
<style>
    [data-testid="stToolbar"] { visibility: hidden !important; display: none !important; }
    header[data-testid="stHeader"] { visibility: hidden !important; display: none !important; }
    #MainMenu { visibility: hidden !important; }
    footer { visibility: hidden !important; }

    .block-container { max-width: 1100px; padding-left: 1rem; padding-right: 1rem; }

    .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 6px 0 14px 0; }
    .metric-card { background: rgba(128,128,128,0.10); border: 1px solid rgba(128,128,128,0.28);
                   border-radius: 10px; padding: 12px; text-align: center; }
    .metric-card .label { font-size: 0.82rem; opacity: 0.75; }
    .metric-card .value { font-size: 1.4rem; font-weight: 700; line-height: 1.35; }
    .metric-card .sub { font-size: 0.8rem; opacity: 0.8; }

    .chip { display: inline-block; padding: 4px 10px; border-radius: 6px; font-weight: 600; font-size: 0.9rem; }
    .chip-present { background: rgba(40,167,69,0.18); border: 1px solid rgba(40,167,69,0.55); }
    .chip-absent { background: rgba(220,53,69,0.18); border: 1px solid rgba(220,53,69,0.55); }
    .chip-noclass { background: rgba(255,193,7,0.22); border: 1px solid rgba(255,193,7,0.65); }
    .chip-visit { background: rgba(23,162,184,0.18); border: 1px solid rgba(23,162,184,0.55); font-weight: 500; }

    .week-card { border: 1px solid rgba(128,128,128,0.28); border-radius: 12px; padding: 12px 14px;
                 margin-bottom: 12px; background: rgba(128,128,128,0.05); }
    .week-card .head { display: flex; justify-content: space-between; align-items: center;
                       flex-wrap: wrap; gap: 6px; font-weight: 700; font-size: 1.05rem; }
    .week-card .row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; margin-top: 8px; }
    .week-card .score { font-size: 1.3rem; font-weight: 700; }
    .week-card .muted { opacity: 0.75; font-size: 0.9rem; }
    .quiz-row { margin-top: 10px; padding-top: 8px; border-top: 1px dashed rgba(128,128,128,0.35); }
    .week-card details { margin-top: 8px; }
    .week-card summary { cursor: pointer; font-weight: 600; }
    .fb { margin-top: 6px; padding: 10px 12px; border-left: 3px solid rgba(23,162,184,0.75);
          background: rgba(23,162,184,0.08); border-radius: 4px; line-height: 1.5; overflow-wrap: anywhere; }
    .note { margin-top: 8px; padding: 10px 12px; border-left: 3px solid rgba(255,193,7,0.85);
            background: rgba(255,193,7,0.10); border-radius: 4px; line-height: 1.5; overflow-wrap: anywhere; }
</style>
""", unsafe_allow_html=True)


# --- Veri yükleme (dosya değişim zamanı önbellek anahtarı) ---
def _mtime(path):
    return os.path.getmtime(path) if os.path.exists(path) else 0


@st.cache_data(ttl=300, show_spinner=False)
def load_all(db_mtime, status_mtime):
    df_all = pl.load_database(DB_FILE)
    status = pl.load_status(STATUS_FILE)
    df_active, hidden = pl.apply_status(df_all, status)
    stats = pl.class_stats(df_active)          # sınıf ortalaması/medyan/dağılım: bir kez hesaplanır
    return df_all, df_active, status, stats, hidden


@st.cache_data(ttl=300, show_spinner=False)
def get_homework(hw_mtime):
    if os.path.exists(HW_FILE):
        xl = pd.ExcelFile(HW_FILE)
        if "All Sections" in xl.sheet_names:
            df_hw = pd.read_excel(HW_FILE, sheet_name="All Sections")
        else:
            dfs = [pd.read_excel(HW_FILE, sheet_name=s) for s in xl.sheet_names]
            df_hw = pd.concat(dfs, ignore_index=True)
        df_hw["Student ID"] = df_hw["Student ID"].apply(clean_id_str)
        return df_hw
    return pd.DataFrame()


df_all, df_db, status_df, STATS, HIDDEN_ROWS = load_all(_mtime(DB_FILE), _mtime(STATUS_FILE))
df_hw = get_homework(_mtime(HW_FILE))


# --- HTML yardımcıları ---
def metric_card(label, value, sub=""):
    sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    return (f'<div class="metric-card"><div class="label">{esc(label)}</div>'
            f'<div class="value">{esc(value)}</div>{sub_html}</div>')


def metric_grid(cards):
    st.markdown('<div class="metric-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def last_updated_caption():
    dt = pl.last_updated(DB_FILE, META_FILE)
    if dt:
        st.caption(f"🕒 Data last updated: {dt.strftime('%d %b %Y, %H:%M')} (Istanbul time)")


def class_week_card(item, section, stats, course, open_feedback):
    """Tek hafta kartı: tek yoklama + o haftanın tüm quiz'leri (bir haftada 2 quiz olabilir)."""
    chip = ('<span class="chip chip-present">✅ Present</span>' if item["attendance"] == "+"
            else '<span class="chip chip-absent">❌ Absent</span>')
    attended = item["attended_section"]
    visit = ""
    if item["attendance"] == "+" and attended and section and attended != section:
        visit = f'<span class="chip chip-visit">📍 Attended the Section {esc(attended)} session</span>'

    quiz_html, can_open = [], open_feedback
    for r in item["quizzes"]:
        q = int(r["quiz_n"])
        bits = [f'<span class="score">{esc(fmt_num(r["quiz_score_num"]))} / 100</span>',
                f'<span class="muted">Quiz {q}</span>']
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

    return (f'<div class="week-card"><div class="head"><span>{esc(item["label"])}</span>'
            f'<span>{chip} {visit}</span></div>{"".join(quiz_html)}</div>')


def no_class_card(item):
    note = str(item["row"].get("note") or "").strip()
    note_html = f'<div class="note"><b>Instructor note:</b> {esc(note)}</div>' if note else ""
    return (f'<div class="week-card"><div class="head"><span>{esc(item["label"])}</span>'
            f'<span class="chip chip-noclass">🚫 No class this week</span></div>'
            f'<div class="muted" style="margin-top:6px">This week does not count as an absence.</div>'
            f'{note_html}</div>')


def trend_chart(frame):
    return (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("quiz_n:O", title="Quiz"),
            y=alt.Y("score:Q", title="Score", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("series:N", title=None),
            tooltip=[alt.Tooltip("quiz_n:O", title="Quiz"), alt.Tooltip("series:N", title=""),
                     alt.Tooltip("score:Q", title="Score")],
        )
        .properties(height=260)
    )


def hist_chart(hist):
    return (
        alt.Chart(hist)
        .mark_bar()
        .encode(
            x=alt.X("bin:N", sort=list(hist["bin"]), title="Score range"),
            y=alt.Y("count:Q", title="Students"),
            color=alt.condition("datum.mine", alt.value("#ff7f0e"), alt.value("#4c78a8")),
            tooltip=[alt.Tooltip("bin:N", title="Range"), alt.Tooltip("count:Q", title="Students")],
        )
        .properties(height=240)
    )


# =========================================================================
# Sol menü
# =========================================================================
st.sidebar.title("Navigation")
portal_mode = st.sidebar.radio("Go to:", ["Student View", "Instructor Dashboard"])


# =========================================================================
# 1. ÖĞRENCİ PORTALI
# =========================================================================
def render_homework_block(sid, cfg):
    hw_cols = [f"HW #{i}" for i in range(1, cfg["hw_count"] + 1)]
    per_point = cfg["hw_pts"] / cfg["hw_count"] / 100.0
    hw_scores = {}
    if not df_hw.empty:
        st_hw = df_hw[df_hw["Student ID"] == sid]
        if not st_hw.empty:
            for col in hw_cols:
                val = st_hw.iloc[0][col] if col in st_hw.columns else None
                if pd.notna(val) and str(val).strip() != "" and str(val).strip().lower() != "pending":
                    try:
                        hw_scores[col] = float(str(val).strip())
                    except ValueError:
                        hw_scores[col] = None
                else:
                    hw_scores[col] = None
    valid = {k: v for k, v in hw_scores.items() if v is not None}
    hw_earned = sum(v * per_point for v in valid.values())
    return hw_cols, hw_scores, valid, hw_earned, per_point


def student_view():
    st.title("🎓 Student Academic Portal")
    st.write("Enter your **Student ID** to view attendance records, quiz results, homework grades, "
             "and weighted course averages.")
    last_updated_caption()

    raw_input = st.text_input("Student ID:", placeholder="e.g. 1111111")
    sid = clean_id_str(raw_input)
    if not sid:
        return

    student_name, enrolled = None, []
    if not df_all.empty:
        m = df_all[df_all["student_id"] == sid]
        if not m.empty:
            student_name = m.iloc[0]["full_name"]
            enrolled.extend(m["course_code"].dropna().unique().tolist())
    if not df_hw.empty:
        m = df_hw[df_hw["Student ID"] == sid]
        if not m.empty:
            student_name = student_name or m.iloc[0]["Full Name"]
            if "MCH2008" not in enrolled:
                enrolled.append("MCH2008")

    if not enrolled:
        st.error("No record found matching this Student ID. Please ensure your ID is entered correctly.")
        return

    st.success(f"Welcome, **{student_name}**!")
    course = st.selectbox("Select Enrolled Course:", enrolled)
    st.markdown(f"### Academic Status: `{course}`")

    rows_all = df_all[(df_all["student_id"] == sid) & (df_all["course_code"] == course)] if not df_all.empty \
        else pd.DataFrame()
    rows = df_db[(df_db["student_id"] == sid) & (df_db["course_code"] == course)] if not df_db.empty \
        else pd.DataFrame()

    section = pl.student_section(rows_all) if not rows_all.empty else ""
    if not section and course == "MCH2008" and not df_hw.empty and "Section" in df_hw.columns:
        h = df_hw[df_hw["Student ID"] == sid]
        section = pl.clean_section(h.iloc[0]["Section"]) if not h.empty else ""

    status_rows = status_df[(status_df["course_code"] == course) & (status_df["section"] == section)] \
        if section else pl.empty_status()

    qscores = pl.student_quiz_scores(rows)          # {quiz_no: puan}; her quiz bir kez sayılır
    tab_over, tab_log, tab_class = st.tabs(["📊 Overview", "🗓️ Weekly Log", "📈 Class Comparison"])

    # ---------------------------------------------------------------- OVERVIEW
    with tab_over:
        cfg = pl.COURSE_CONFIG.get(course)
        scores = list(qscores.values())

        if cfg:
            hw_cols, hw_scores, valid, hw_earned, per_point = render_homework_block(sid, cfg)
            best, qpts = cfg["best_quizzes"], cfg["quiz_pts"]
            quiz_earned = pl.quiz_points(scores, best, qpts) if scores else 0.0
            quiz_avg = (sum(sorted(scores, reverse=True)[:best]) / len(sorted(scores, reverse=True)[:best])) \
                if scores else None
            total = hw_earned + quiz_earned
            base = cfg["hw_pts"] + cfg["quiz_pts"]

            st.markdown("#### 📊 Course Performance & Standing")
            metric_grid([
                metric_card(f"Homework Contribution (%{cfg['hw_pts']:.0f})",
                            f"{hw_earned:.2f} / {cfg['hw_pts']:.2f} pts",
                            f"{len(valid)}/{cfg['hw_count']} evaluated" if valid else "Pending"),
                metric_card(f"Best {best} Quizzes Avg (%{cfg['quiz_pts']:.0f})",
                            f"{quiz_avg:.1f} / 100" if quiz_avg is not None else "Pending",
                            f"+{quiz_earned:.2f} / {cfg['quiz_pts']:.2f} pts earned" if quiz_avg is not None else ""),
                metric_card(f"Current Standing (%{base:.0f} Base)", f"{total:.2f} / {base:.2f} pts"),
            ])
            st.caption(f"Your final grade counts your best {best} of {cfg['total_quizzes']} quizzes, "
                       f"worth {cfg['quiz_pts']:.0f} points in total.")

            st.markdown("##### 📝 Homework Breakdown")
            hw_cards = []
            for col in hw_cols:
                v = hw_scores.get(col)
                if v is not None:
                    hw_cards.append(metric_card(col, f"{fmt_num(v)} / 100", f"+{v * per_point:.2f} pts"))
                else:
                    hw_cards.append(metric_card(col, "Pending"))
            metric_grid(hw_cards)

        trend = pl.trend_frame(rows, STATS, course) if not rows.empty else pd.DataFrame()
        if not trend.empty:
            st.markdown("#### 📈 Your Quiz Scores vs. Class Average")
            st.altair_chart(trend_chart(trend), use_container_width=True)
        elif not cfg:
            st.info("No quiz scores published for this course yet.")

        # ---- What-if
        if cfg:
            held = pl.held_quizzes(df_db, course, section) | set(qscores)
            remaining = pl.remaining_quizzes(cfg, held)

            st.markdown("#### 🎯 What-if Calculator")
            if remaining == 0:
                st.info("All quizzes for this course have been held, so there is nothing left to simulate.")
            else:
                default = int(pl.half_up(sum(scores) / len(scores))) if scores else 70
                x = st.slider(
                    f"If I score on average ... on the {remaining} remaining quiz(zes):",
                    0, 100, default, key=f"whatif_{sid}_{course}",
                )
                res = pl.what_if(scores, remaining, x, cfg)
                metric_grid([
                    metric_card("Quiz points now", f"{res['current']:.2f} / {cfg['quiz_pts']:.2f}"),
                    metric_card("Projected quiz points", f"{res['projected']:.2f} / {cfg['quiz_pts']:.2f}",
                                f"{res['projected'] - res['current']:+.2f} vs. now"),
                    metric_card(f"Projected standing (%{cfg['hw_pts'] + cfg['quiz_pts']:.0f} base)",
                                f"{hw_earned + res['projected']:.2f} / {cfg['hw_pts'] + cfg['quiz_pts']:.2f}",
                                f"max possible {hw_earned + res['max']:.2f}"),
                ])
                st.caption(f"Only your best {cfg['best_quizzes']} of {cfg['total_quizzes']} quizzes count. "
                           "A cancelled class does not change the total number of quizzes. "
                           "This is an estimate, not an official grade.")

    # ---------------------------------------------------------------- WEEKLY LOG
    with tab_log:
        st.markdown("#### 🗓️ Weekly Attendance & Quiz Log")
        timeline = pl.build_timeline(rows, status_rows, newest_first=NEWEST_FIRST)
        if not timeline:
            st.info(f"No weekly attendance or quiz records published for `{course}` yet.")
        else:
            opened = False
            parts = []
            for item in timeline:
                if item["type"] == "no_class":
                    parts.append(no_class_card(item))
                else:
                    has_fb = any(pd.notna(r.get("feedback")) and str(r.get("feedback")).strip() != ""
                                 for r in item["quizzes"])
                    parts.append(class_week_card(item, section, STATS, course, open_feedback=(has_fb and not opened)))
                    opened = opened or has_fb
            st.markdown("".join(parts), unsafe_allow_html=True)

    # ---------------------------------------------------------------- CLASS COMPARISON
    with tab_class:
        st.markdown("#### 📈 How did the class do?")
        quizzes = sorted(q for (c, q), s in STATS.items() if c == course and s["n"] >= pl.MIN_CLASS_FOR_CHART)
        if not quizzes:
            st.info("The class distribution will appear here once enough quizzes have been evaluated.")
        else:
            def _qlabel(q):
                w = STATS[(course, q)]["week_no"]
                return f"Quiz {q}" + (f" (Week {w})" if w else "")
            q_sel = st.selectbox("Select quiz:", quizzes, index=len(quizzes) - 1, format_func=_qlabel,
                                 key=f"dist_quiz_{sid}_{course}")
            s = STATS[(course, q_sel)]
            mine = qscores.get(q_sel)
            hist = pl.histogram(s["scores"])
            hist["mine"] = hist["start"].eq(pl.bin_index(mine) * 10) if mine is not None else False
            metric_grid([
                metric_card("Your score", f"{fmt_num(mine)} / 100" if mine is not None else "N/A"),
                metric_card("Class average", f"{s['mean']:.1f}"),
                metric_card("Class median", fmt_num(s["median"])),
                metric_card("Students evaluated", str(s["n"])),
            ])
            st.altair_chart(hist_chart(hist), use_container_width=True)
            if mine is not None:
                st.caption("The orange bar is the score range you are in.")


# =========================================================================
# 2. ÖĞRETMEN PANELİ
# =========================================================================
def persist_status(new_status, message):
    """Önce yerel dosyaya yazar, GitHub ayarları varsa repoya da commit eder."""
    pl.write_status(STATUS_FILE, new_status)
    token, repo = get_secret("GITHUB_TOKEN"), get_secret("GITHUB_REPO")
    if not (token and repo):
        return "local", None
    try:
        pl.github_put_file(token, repo, get_secret("GITHUB_STATUS_PATH") or STATUS_FILE,
                           pl.status_to_csv_bytes(new_status), message,
                           branch=get_secret("GITHUB_BRANCH") or "main")
        return "github", None
    except Exception as e:  # noqa: BLE001
        return "local", str(e)


def show_flash():
    flash = st.session_state.pop("flash", None) if hasattr(st.session_state, "pop") else None
    if flash:
        kind, text = flash
        {"success": st.success, "warning": st.warning, "error": st.error}.get(kind, st.info)(text)


def class_status_tab(all_courses):
    st.subheader("🚫 No Class This Week")
    st.write("Mark a week as **No Class** for selected course sections (holiday, instructor absence, etc.). "
             "Students see a notice with your note instead of an absence, and that week is excluded from "
             "attendance and quiz statistics. The total number of quizzes does not change.")
    show_flash()

    if not (get_secret("GITHUB_TOKEN") and get_secret("GITHUB_REPO")):
        st.info("GitHub is not configured (`GITHUB_TOKEN`, `GITHUB_REPO` in Secrets). Changes are saved on the "
                "server only and **will be lost when the app restarts**. Configure the secrets to make them permanent.")

    course = st.selectbox("Course:", all_courses, key="ns_course")
    sections = sorted({s for s in df_all.loc[df_all["course_code"] == course, "section"] if s}) \
        if not df_all.empty else []
    sections = sections or ["901", "902"]
    course_weeks = df_all.loc[df_all["course_code"] == course, "week_no"].dropna() if not df_all.empty else []
    guess = int(max(course_weeks)) + 1 if len(course_weeks) else 1

    with st.form("no_class_form"):
        week_no = st.number_input("Week number:", min_value=1, max_value=20, value=min(guess, 20), step=1)
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
            mode, err = persist_status(
                new_status, f"No class: {course} Week {int(week_no)} Sections {', '.join(chosen)}")
            st.cache_data.clear()
            msg = f"Marked {course} Week {int(week_no)} as No Class for Section(s) {', '.join(chosen)}."
            if existing:
                msg += f" {existing} existing record(s) for this week are now hidden from students and statistics."
            if mode == "github":
                st.session_state["flash"] = ("success", msg + " Saved to GitHub; the app may restart in a minute "
                                                             "(you may need to log in again).")
            elif err:
                st.session_state["flash"] = ("warning", msg + f" Saved on the server only (GitHub error: {err}).")
            else:
                st.session_state["flash"] = ("warning", msg + " Saved on the server only (not permanent).")
            st.rerun()

    st.markdown("##### Current No Class entries")
    if status_df.empty:
        st.caption("No entries yet.")
    else:
        view = status_df.rename(columns={"course_code": "Course", "week_no": "Week", "section": "Section",
                                         "note": "Note", "created_at": "Created"})
        st.dataframe(view, use_container_width=True, hide_index=True)
        labels = {f"{r.course_code} · Week {r.week_no} · Section {r.section}": (r.course_code, int(r.week_no), r.section)
                  for r in status_df.itertuples()}
        picked = st.multiselect("Select entries to remove:", list(labels), key="ns_remove")
        if st.button("Remove selected") and picked:
            new_status = pl.remove_status(status_df, [labels[p] for p in picked])
            mode, err = persist_status(new_status, f"Remove {len(picked)} no-class entr{'y' if len(picked) == 1 else 'ies'}")
            st.cache_data.clear()
            st.session_state["flash"] = ("success" if mode == "github" else "warning",
                                         f"Removed {len(picked)} entr{'y' if len(picked) == 1 else 'ies'}."
                                         + ("" if mode == "github" else " Saved on the server only."))
            st.rerun()


def analytics_tab(sel_course):
    if sel_course == "MCH2008" and not df_hw.empty:
        st.subheader("📚 MCH2008 Homework Overview")
        hw_summary = []
        for h in ["HW #1", "HW #2", "HW #3", "HW #4", "HW #5"]:
            if h in df_hw.columns:
                vals = pd.to_numeric(df_hw[h], errors="coerce").dropna()
                hw_summary.append({
                    "Homework": h, "Submitted Count": len(vals),
                    "Average Score": f"{vals.mean():.1f}" if not vals.empty else "N/A",
                    "Highest Score": f"{vals.max():.0f}" if not vals.empty else "N/A",
                })
        if hw_summary:
            st.dataframe(pd.DataFrame(hw_summary), use_container_width=True)
        st.divider()

    course_db = df_db[df_db["course_code"] == sel_course] if not df_db.empty else pd.DataFrame()
    if course_db.empty:
        st.info(f"No weekly records uploaded for `{sel_course}` yet.")
        return

    ordered = course_db.sort_values("week_no", na_position="last")
    weeks_avail = ordered["week"].dropna().unique().tolist()          # Week 1, Week 2, ... (sayısal sıra)
    sel_week = st.selectbox("Select Academic Week:", weeks_avail)
    week_data = course_db[course_db["week"] == sel_week].copy()

    wk_no = pl.week_number(sel_week)
    cancelled = status_df[(status_df["course_code"] == sel_course) & (status_df["week_no"] == wk_no)]
    if not cancelled.empty:
        secs = ", ".join(cancelled["section"])
        st.warning(f"No class this week for Section(s): {secs}. These sections are excluded from the statistics below.")

    st.subheader(f"📊 Analytics Summary: {sel_course} - {sel_week}")
    wk_att = pl.week_attendance(week_data)             # haftada 2 quiz olsa da öğrenci tek sayılır
    total_st = len(wk_att)
    att_count = int((wk_att["attendance"] == "+").sum())
    att_rate = (att_count / total_st * 100) if total_st else 0

    scored = week_data[week_data["quiz_n"].notna() & week_data["quiz_score_num"].notna()]
    qtable = (scored.groupby("quiz_n")["quiz_score_num"].agg(["count", "mean", "median"]).reset_index()
              if not scored.empty else pd.DataFrame())
    n_quizzes = len(qtable)
    if n_quizzes == 1:
        avg_txt = f"{qtable.iloc[0]['mean']:.2f} / 100"
    else:
        avg_txt = "See table" if n_quizzes > 1 else "N/A"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Enrolled Students", total_st)
    m2.metric("Attendance", f"{att_count} ({att_rate:.1f}%)")
    m3.metric("Quizzes This Week", n_quizzes)
    m4.metric("Class Average", avg_txt)
    if n_quizzes:
        st.dataframe(pd.DataFrame({
            "Quiz": [f"Quiz {int(q)}" for q in qtable["quiz_n"]],
            "Evaluated": qtable["count"].astype(int),
            "Average": qtable["mean"].round(1),
            "Median": qtable["median"].round(1),
        }), use_container_width=True, hide_index=True)
    st.divider()

    st.markdown("#### 📋 Detailed Student Roster")
    cols_show = ["student_id", "full_name", "section", "attendance", "quiz_no", "quiz_score", "feedback"]
    if week_data["attended_section"].astype(str).str.strip().ne("").any():
        cols_show.insert(3, "attended_section")
    st.dataframe(week_data[cols_show], use_container_width=True)


def instructor_view():
    st.title("🔒 Instructor Analytics Dashboard")

    if "instructor_auth" not in st.session_state:
        st.session_state.instructor_auth = False

    if not st.session_state.instructor_auth:
        pwd = st.text_input("Enter Instructor Password:", type="password")
        if st.button("Login"):
            if pwd == INSTRUCTOR_PASSWORD:
                st.session_state.instructor_auth = True
                st.rerun()
            else:
                st.error("Invalid password. Access denied.")
        return

    if st.sidebar.button("Logout"):
        st.session_state.instructor_auth = False
        st.rerun()

    all_courses = set()
    if not df_all.empty:
        all_courses.update(df_all["course_code"].dropna().unique().tolist())
    if not df_hw.empty:
        all_courses.add("MCH2008")
    all_courses = sorted(all_courses)

    tab_an, tab_status = st.tabs(["📊 Analytics", "🚫 No Class This Week"])
    with tab_an:
        sel_course = st.selectbox("Select Course to Analyze:", all_courses)
        analytics_tab(sel_course)
    with tab_status:
        class_status_tab(all_courses)


if portal_mode == "Student View":
    student_view()
else:
    instructor_view()