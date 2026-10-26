import streamlit as st
import pandas as pd
import numpy as np
import os

# --- Sayfa Yapılandırması ---
st.set_page_config(
    page_title="Academic Portal - BAU Mechatronics", 
    page_icon="🎓", 
    layout="wide"
)

# --- Özel CSS Tasarımı & Araç Çubuğunu (GitHub İkonunu) Gizleme ---
st.markdown("""
<style>
    /* Sağ üstteki GitHub simgesi, düzenleme ve araç çubuğunu gizler */
    [data-testid="stToolbar"] {
        visibility: hidden !important;
        display: none !important;
    }
    header[data-testid="stHeader"] {
        visibility: hidden !important;
        display: none !important;
    }
    #MainMenu {
        visibility: hidden !important;
    }
    footer {
        visibility: hidden !important;
    }

    /* Yoklama ve Metrik Kutuları */
    .present-box { 
        background-color: #d4edda; 
        color: #155724; 
        padding: 8px 14px; 
        border-radius: 6px; 
        font-weight: 600; 
        display: inline-block; 
        border: 1px solid #c3e6cb;
    }
    .absent-box { 
        background-color: #f8d7da; 
        color: #721c24; 
        padding: 8px 14px; 
        border-radius: 6px; 
        font-weight: 600; 
        display: inline-block; 
        border: 1px solid #f5c6cb;
    }
    .metric-container {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        padding: 12px;
        border-radius: 8px;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# Dosya Yolları ve Şifre
DB_FILE = "grades_database.xlsx"
HW_FILE = "MCH2008_homework.xlsx"
INSTRUCTOR_PASSWORD = "a123b456c789++"

# --- Yardımcı Fonksiyonlar ---
def clean_id_str(val):
    if pd.isna(val) or val is None:
        return ""
    # Excel float (.0), boşluk ve rakam dışı karakterleri temizler
    s = str(val).split('.')[0].strip()
    return ''.join(filter(str.isdigit, s))

# --- Veri Yükleme Fonksiyonları ---
@st.cache_data(ttl=10)
def get_database():
    if os.path.exists(DB_FILE):
        df = pd.read_excel(DB_FILE)
        
        # Sütun isimlerini sanitize etme (boşluklar, büyük/küçük harf uyumsuzlukları)
        raw_cols = [str(c).strip() for c in df.columns]
        rename_map = {}
        for c in raw_cols:
            c_clean = c.lower().replace(" ", "_")
            if "course" in c_clean or "ders" in c_clean:
                rename_map[c] = "course_code"
            elif "student" in c_clean or "ogrenci" in c_clean or "öğrenci" in c_clean:
                rename_map[c] = "student_id"
            elif "full" in c_clean or "name" in c_clean or "isim" in c_clean or "ad" in c_clean:
                rename_map[c] = "full_name"
            elif "sec" in c_clean or "şube" in c_clean:
                rename_map[c] = "section"
            elif "week" in c_clean or "hafta" in c_clean:
                rename_map[c] = "week"
            elif "att" in c_clean or "yoklama" in c_clean:
                rename_map[c] = "attendance"
            elif "quiz_no" in c_clean or "quiz_num" in c_clean or "quiz_no" in c:
                rename_map[c] = "quiz_no"
            elif "score" in c_clean or "not" in c_clean or "puan" in c_clean:
                rename_map[c] = "quiz_score"
            elif "feed" in c_clean or "geribildirim" in c_clean or "yorum" in c_clean:
                rename_map[c] = "feedback"
                
        if rename_map:
            df = df.rename(columns=rename_map)

        # Öğrenci numarasını temizle ve sayısal quiz notu sütununu ekle
        if "student_id" in df.columns:
            df["student_id"] = df["student_id"].apply(clean_id_str)
        if "quiz_score" in df.columns:
            df["quiz_score_num"] = pd.to_numeric(df["quiz_score"], errors="coerce")
        else:
            df["quiz_score_num"] = np.nan
            
        return df
    return pd.DataFrame()

@st.cache_data(ttl=10)
def get_homework():
    if os.path.exists(HW_FILE):
        xl = pd.ExcelFile(HW_FILE)
        if "All Sections" in xl.sheet_names:
            df_hw = pd.read_excel(HW_FILE, sheet_name="All Sections")
        else:
            dfs = [pd.read_excel(HW_FILE, sheet_name=s) for s in xl.sheet_names]
            df_hw = pd.concat(dfs, ignore_index=True)
            
        # Sütun isimlerini kontrol et ve temizle
        for col in df_hw.columns:
            c_lower = str(col).lower()
            if "student" in c_lower or "id" in c_lower:
                df_hw = df_hw.rename(columns={col: "Student ID"})
            elif "name" in c_lower or "ad" in c_lower:
                df_hw = df_hw.rename(columns={col: "Full Name"})
                
        if "Student ID" in df_hw.columns:
            df_hw["Student ID"] = df_hw["Student ID"].apply(clean_id_str)
        return df_hw
    return pd.DataFrame()

df_db = get_database()
df_hw = get_homework()

# --- Sol Navigasyon Menüsü ---
st.sidebar.title("Navigation")
portal_mode = st.sidebar.radio("Go to:", ["Student View", "Instructor Dashboard"])

# =========================================================================
# 1. ÖĞRENCİ PORTALI (STUDENT VIEW)
# =========================================================================
if portal_mode == "Student View":
    st.title("🎓 Student Academic Portal")
    st.write("Enter your **Student ID** to view attendance records, quiz results, homework grades, and weighted course averages.")

    raw_input = st.text_input("Student ID:", placeholder="e.g. 1111111")
    student_id_input = clean_id_str(raw_input)

    if student_id_input:
        student_name = None
        enrolled_courses = []

        if not df_db.empty and "student_id" in df_db.columns:
            match_db = df_db[df_db["student_id"] == student_id_input]
            if not match_db.empty:
                if "full_name" in match_db.columns:
                    student_name = match_db.iloc[0]["full_name"]
                if "course_code" in match_db.columns:
                    enrolled_courses.extend(match_db["course_code"].dropna().unique().tolist())

        if not df_hw.empty and "Student ID" in df_hw.columns:
            match_hw = df_hw[df_hw["Student ID"] == student_id_input]
            if not match_hw.empty:
                if not student_name and "Full Name" in match_hw.columns:
                    student_name = match_hw.iloc[0]["Full Name"]
                if "MCH2008" not in enrolled_courses:
                    enrolled_courses.append("MCH2008")

        if not enrolled_courses:
            st.error("No record found matching this Student ID. Please ensure your ID is entered correctly.")
        else:
            st.success(f"Welcome, **{student_name if student_name else 'Student'}**!")
            selected_course = st.selectbox("Select Enrolled Course:", enrolled_courses)

            st.markdown(f"### Academic Status: `{selected_course}`")
            st.divider()

            # --- MCH 2008: ÖDEVLER VE EN YÜKSEK 7 QUİZ SİSTEMİ ---
            if selected_course == "MCH2008":
                hw_cols = ["HW #1", "HW #2", "HW #3", "HW #4", "HW #5"]
                hw_scores = {}

                if not df_hw.empty and "Student ID" in df_hw.columns:
                    st_hw = df_hw[df_hw["Student ID"] == student_id_input]
                    if not st_hw.empty:
                        for col in hw_cols:
                            if col in st_hw.columns and pd.notna(st_hw.iloc[0][col]):
                                val_raw = str(st_hw.iloc[0][col]).strip()
                                if val_raw != "" and val_raw.lower() != "pending":
                                    try:
                                        hw_scores[col] = float(val_raw)
                                    except ValueError:
                                        hw_scores[col] = None
                                else:
                                    hw_scores[col] = None
                            else:
                                hw_scores[col] = None

                # 1. Ödev Katkısı (5 ödev var, her biri %4 ağırlıkta)
                valid_hws = {k: v for k, v in hw_scores.items() if v is not None}
                hw_count = len(valid_hws)
                hw_earned = sum(v * 0.04 for v in valid_hws.values())

                # 2. Quiz Katkısı (En yüksek 7 quiz alınır, her quiz 20/7 ≈ 2.857 puan kazandırır)
                quiz_scores = []
                if not df_db.empty and "student_id" in df_db.columns and "course_code" in df_db.columns:
                    mch08_db = df_db[(df_db["student_id"] == student_id_input) & (df_db["course_code"] == "MCH2008")]
                    if "quiz_score_num" in mch08_db.columns:
                        quiz_scores = mch08_db["quiz_score_num"].dropna().tolist()

                quiz_avg = None
                quiz_earned = 0.0

                if quiz_scores:
                    sorted_q = sorted(quiz_scores, reverse=True)[:7]
                    quiz_avg = sum(sorted_q) / len(sorted_q)
                    quiz_earned = sum(q * (0.20 / 7.0) for q in sorted_q)

                total_earned = hw_earned + quiz_earned

                st.markdown("#### 📊 Course Performance & Standing")
                o1, o2, o3 = st.columns(3)
                with o1:
                    st.metric(
                        label="Homework Contribution (%20)", 
                        value=f"{hw_earned:.2f} / 20.00 pts", 
                        delta=f"{hw_count}/5 evaluated" if hw_count > 0 else "Pending"
                    )
                with o2:
                    q_str = f"{quiz_avg:.1f} / 100" if quiz_avg is not None else "Pending"
                    st.metric(
                        label="Best 7 Quizzes Avg (%20)", 
                        value=q_str, 
                        delta=f"+{quiz_earned:.2f} / 20.00 pts earned" if quiz_avg is not None else None
                    )
                with o3:
                    st.metric(
                        label="Current Standing (%40 Base)", 
                        value=f"{total_earned:.2f} / 40.00 pts"
                    )

                st.markdown("##### 📝 Homework Breakdown")
                cols = st.columns(5)
                for idx, col_name in enumerate(hw_cols):
                    with cols[idx]:
                        val = hw_scores.get(col_name)
                        if val is not None:
                            pts = val * 0.04
                            score_html = f"<b>{int(val)} / 100</b><br><small style='color:green'>+{pts:.2f} pts</small>"
                        else:
                            score_html = "<small style='color:gray'><i>Pending</i></small>"
                        st.markdown(f"<div class='metric-container'><b>{col_name}</b><br>{score_html}</div>", unsafe_allow_html=True)

                st.divider()

            # --- HAFTALIK YOKLAMA VE QUIZ LOGLARI (TÜM DERSLER) ---
            st.markdown("#### 🗓️ Weekly Attendance & Quiz Log")
            
            course_logs = pd.DataFrame()
            if not df_db.empty and "student_id" in df_db.columns and "course_code" in df_db.columns:
                course_logs = df_db[(df_db["student_id"] == student_id_input) & (df_db["course_code"] == selected_course)]

            if course_logs.empty:
                st.info(f"No weekly attendance or quiz records published for `{selected_course}` yet.")
            else:
                for _, row in course_logs.iterrows():
                    week = row.get("week", "N/A")
                    att = str(row.get("attendance", "")).strip()
                    q_label = str(row.get("quiz_no", "")).strip()
                    score = row.get("quiz_score", np.nan)
                    feedback = row.get("feedback", "")

                    has_quiz = (q_label != "No Quiz") and pd.notna(score) and (str(score).strip() != "")

                    with st.container():
                        st.markdown(f"##### {week}")
                        col1, col2, col3 = st.columns([1.5, 1.5, 2.5])

                        with col1:
                            if att == "+":
                                st.markdown("<div class='present-box'>✅ Present</div>", unsafe_allow_html=True)
                            else:
                                st.markdown("<div class='absent-box'>❌ Absent</div>", unsafe_allow_html=True)
                            
                            if not has_quiz:
                                st.caption("No quiz evaluated this week.")

                        with col2:
                            if has_quiz:
                                try:
                                    st.metric(label=f"Your {q_label} Score", value=f"{int(float(score))} / 100")
                                except ValueError:
                                    st.metric(label=f"Your {q_label} Score", value=f"{score} / 100")
                                    
                                week_all = df_db[(df_db["course_code"] == selected_course) & (df_db["week"] == week)]
                                if "quiz_score_num" in week_all.columns:
                                    q_scores_all = week_all["quiz_score_num"].dropna()
                                    if not q_scores_all.empty:
                                        st.caption(f"Class Average: **{q_scores_all.mean():.1f} / 100**")
                            else:
                                st.caption("Quiz Score: N/A")

                        with col3:
                            if has_quiz and pd.notna(feedback) and str(feedback).strip() != "":
                                st.info(f"**Instructor Feedback:**\n\n{feedback}")

                        st.divider()

# =========================================================================
# 2. ÖĞRETMEN ANALİTİK PANELİ (INSTRUCTOR DASHBOARD)
# =========================================================================
else:
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
    else:
        if st.sidebar.button("Logout"):
            st.session_state.instructor_auth = False
            st.rerun()

        all_courses = set()
        if not df_db.empty and "course_code" in df_db.columns:
            all_courses.update(df_db["course_code"].dropna().unique().tolist())
        if not df_hw.empty:
            all_courses.add("MCH2008")

        sel_course = st.selectbox("Select Course to Analyze:", sorted(list(all_courses)))

        # MCH2008 Seçildiyse Ödev İstatistikleri
        if sel_course == "MCH2008" and not df_hw.empty:
            st.subheader("📚 MCH2008 Homework Overview")
            hw_summary = []
            for h in ["HW #1", "HW #2", "HW #3", "HW #4", "HW #5"]:
                if h in df_hw.columns:
                    valid_vals = pd.to_numeric(df_hw[h], errors="coerce").dropna()
                    hw_summary.append({
                        "Homework": h,
                        "Submitted Count": len(valid_vals),
                        "Average Score": f"{valid_vals.mean():.1f}" if not valid_vals.empty else "N/A",
                        "Highest Score": f"{valid_vals.max():.0f}" if not valid_vals.empty else "N/A"
                    })
            if hw_summary:
                st.dataframe(pd.DataFrame(hw_summary), use_container_width=True)
            st.divider()

        # Haftalık Veritabanı İstatistikleri
        course_db = pd.DataFrame()
        if not df_db.empty and "course_code" in df_db.columns:
            course_db = df_db[df_db["course_code"] == sel_course]

        if course_db.empty:
            st.info(f"No weekly records uploaded for `{sel_course}` yet.")
        else:
            weeks_avail = course_db["week"].dropna().unique().tolist() if "week" in course_db.columns else []
            sel_week = st.selectbox("Select Academic Week:", weeks_avail)
            week_data = course_db[course_db["week"] == sel_week].copy()

            st.subheader(f"📊 Analytics Summary: {sel_course} - {sel_week}")

            total_st = len(week_data)
            att_count = len(week_data[week_data["attendance"] == "+"]) if "attendance" in week_data.columns else 0
            att_rate = (att_count / total_st * 100) if total_st > 0 else 0

            q_count = 0
            q_mean = 0.0
            if "quiz_score_num" in week_data.columns:
                quiz_scores = week_data["quiz_score_num"].dropna()
                q_count = len(quiz_scores)
                q_mean = quiz_scores.mean() if q_count > 0 else 0.0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Enrolled Students", total_st)
            m2.metric("Attendance", f"{att_count} ({att_rate:.1f}%)")
            m3.metric("Quizzes Evaluated", q_count)
            m4.metric("Class Average", f"{q_mean:.2f} / 100" if q_count > 0 else "N/A")

            st.divider()

            st.markdown("#### 📋 Detailed Student Roster")
            cols_show = [c for c in ["student_id", "full_name", "section", "attendance", "quiz_no", "quiz_score", "feedback"] if c in week_data.columns]
            st.dataframe(week_data[cols_show], use_container_width=True)
