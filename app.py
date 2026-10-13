import streamlit as st
import pandas as pd
import numpy as np
import os
import json
import hashlib

# --- SAYFA YAPILANDIRMASI ---
st.set_page_config(
    page_title="Academic Portal - BAU Mechatronics", 
    page_icon="🎓", 
    layout="wide"
)

# --- ARAYÜZ CSS ÖZELLEŞTİRMELERİ ---
st.markdown("""
<style>
    /* Sağ üstteki GitHub simgesi, düzenleme ve araç çubuğunu gizler */
    [data-testid="stToolbar"], header[data-testid="stHeader"], #MainMenu, footer {
        visibility: hidden !important;
        display: none !important;
    }
    .present-box { 
        background-color: #d4edda; color: #155724; padding: 8px 14px; 
        border-radius: 6px; font-weight: 600; display: inline-block; border: 1px solid #c3e6cb;
    }
    .absent-box { 
        background-color: #f8d7da; color: #721c24; padding: 8px 14px; 
        border-radius: 6px; font-weight: 600; display: inline-block; border: 1px solid #f5c6cb;
    }
    .metric-container {
        background-color: #f8f9fa; border: 1px solid #e9ecef;
        padding: 12px; border-radius: 8px; text-align: center;
    }
</style>
""", unsafe_allow_html=True)

# --- DOSYA YOLLARI ---
DB_FILE = "grades_database.xlsx"
HW_FILE = "MCH2008_homework.xlsx"
INSTRUCTORS_JSON = "instructors.json"
CONFIGS_JSON = "course_configs.json"

# --- YARDIMCI GÜVENLİK VE VERİ FONKSİYONLARI ---
def hash_pwd(password):
    """Şifreleri SHA-256 ile güvenli biçimde özetler."""
    return hashlib.sha256(password.encode()).hexdigest()

def load_json(filepath, default_data):
    """JSON dosyasını okur, dosya yoksa varsayılan veriyi döndürür."""
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default_data
    return default_data

def save_json(filepath, data):
    """Veriyi JSON dosyasına kaydeder."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Varsayılan Akademisyen Veritabanı Kurulumu
default_instructors = {
    "gokhangulser": {
        "name": "Gökhan Gülser",
        "password": hash_pwd("bak34++"),
        "email": "gokhan.gulser@bau.edu.tr"
    }
}

instructors_db = load_json(INSTRUCTORS_JSON, default_instructors)
course_configs_db = load_json(CONFIGS_JSON, {})

@st.cache_data(ttl=10)
def get_database():
    if os.path.exists(DB_FILE):
        df = pd.read_excel(DB_FILE)
        df["quiz_score_num"] = pd.to_numeric(df["quiz_score"], errors="coerce")
        df["student_id"] = df["student_id"].astype(str).str.strip()
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
        df_hw["Student ID"] = df_hw["Student ID"].astype(str).str.strip()
        return df_hw
    return pd.DataFrame()

df_db = get_database()
df_hw = get_homework()

# --- NAVİGASYON VE OTURUM KONTROLÜ ---
st.sidebar.title("🎓 Portal Navigation")
portal_mode = st.sidebar.radio("Go to:", ["Student View", "Instructor Portal"])

# =========================================================================
# 1. ÖĞRENCİ PORTALI (STUDENT VIEW)
# =========================================================================
if portal_mode == "Student View":
    st.title("🎓 Student Academic Portal")
    st.write("Enter your **Student ID** to view attendance records, quiz results, homework grades, and weighted course averages.")

    student_id_input = st.text_input("Student ID:", placeholder="e.g. 2359481").strip()

    if student_id_input:
        student_name = None
        enrolled_courses = []

        if not df_db.empty:
            match_db = df_db[df_db["student_id"] == student_id_input]
            if not match_db.empty:
                student_name = match_db.iloc[0]["full_name"]
                enrolled_courses.extend(match_db["course_code"].dropna().unique().tolist())

        if not df_hw.empty:
            match_hw = df_hw[df_hw["Student ID"] == student_id_input]
            if not match_hw.empty:
                if not student_name:
                    student_name = match_hw.iloc[0]["Full Name"]
                if "MCH2008" not in enrolled_courses:
                    enrolled_courses.append("MCH2008")

        if not enrolled_courses:
            st.error("No record found matching this Student ID. Please ensure your ID is entered correctly.")
        else:
            st.success(f"Welcome, **{student_name}**!")
            selected_course = st.selectbox("Select Enrolled Course:", enrolled_courses)

            st.markdown(f"### Academic Status: `{selected_course}`")
            st.divider()

            # --- MCH2008 / DERS DEĞERLENDİRME HESAPLAMALARI ---
            if selected_course == "MCH2008":
                hw_cols = ["HW #1", "HW #2", "HW #3", "HW #4", "HW #5"]
                hw_scores = {}

                if not df_hw.empty:
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

                # 1. Ödev Katkısı (5 Ödev var, her biri %4 ağırlıkta)
                valid_hws = {k: v for k, v in hw_scores.items() if v is not None}
                hw_count = len(valid_hws)
                hw_earned = sum(v * 0.04 for v in valid_hws.values())  # Not * 0.04

                # 2. Quiz Katkısı (En yüksek 7 quiz alınır, her quiz 20/7 ≈ 2.857 puan kazandırır)
                quiz_scores = []
                if not df_db.empty:
                    mch08_db = df_db[(df_db["student_id"] == student_id_input) & (df_db["course_code"] == "MCH2008")]
                    quiz_scores = mch08_db["quiz_score_num"].dropna().tolist()

                quiz_avg = None
                quiz_earned = 0.0

                if quiz_scores:
                    sorted_q = sorted(quiz_scores, reverse=True)[:7]
                    quiz_avg = sum(sorted_q) / len(sorted_q)
                    # Her quiz notunun 20/7 puanlık katkısı hesaplanır
                    quiz_earned = sum(q * (0.20 / 7.0) for q in sorted_q)

                # 3. Toplam Kazanılan Puan (Ödevler %20 + Quizler %20 = Toplam %40)
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
            if not df_db.empty:
                course_logs = df_db[(df_db["student_id"] == student_id_input) & (df_db["course_code"] == selected_course)]

            if course_logs.empty:
                st.info(f"No weekly attendance or quiz records published for `{selected_course}` yet.")
            else:
                for _, row in course_logs.iterrows():
                    week = row["week"]
                    att = str(row["attendance"]).strip()
                    q_label = str(row["quiz_no"]).strip()
                    score = row["quiz_score"]
                    feedback = row["feedback"]

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
                                st.metric(label=f"Your {q_label} Score", value=f"{int(float(score))} / 100")
                                week_all = df_db[(df_db["course_code"] == selected_course) & (df_db["week"] == week)]
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
# 2. ÖĞRETMEN PORTALI & YÖNETİM PANELİ (INSTRUCTOR PORTAL)
# =========================================================================
else:
    st.title("🔒 Instructor Portal")

    # Oturum Durumu (Session State)
    if "logged_in_user" not in st.session_state:
        st.session_state.logged_in_user = None

    # --- A. GİRİŞ VEYA ŞİFRE SIFIRLAMA EKRANI ---
    if st.session_state.logged_in_user is None:
        auth_tab1, auth_tab2 = st.tabs(["🔑 Instructor Login", "🔄 Reset / Change Password"])

        with auth_tab1:
            st.subheader("Sign In")
            username_inp = st.text_input("Username:").strip().lower()
            pwd_inp = st.text_input("Password:", type="password")
            
            if st.button("Login", type="primary"):
                if username_inp in instructors_db and instructors_db[username_inp]["password"] == hash_pwd(pwd_inp):
                    st.session_state.logged_in_user = username_inp
                    st.success(f"Welcome, {instructors_db[username_inp]['name']}!")
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

        with auth_tab2:
            st.subheader("Reset Password")
            reset_user = st.text_input("Username for Reset:").strip().lower()
            old_pwd = st.text_input("Current Password:", type="password", key="old_p")
            new_pwd = st.text_input("New Password:", type="password", key="new_p")
            confirm_pwd = st.text_input("Confirm New Password:", type="password", key="conf_p")

            if st.button("Update Password"):
                if reset_user in instructors_db and instructors_db[reset_user]["password"] == hash_pwd(old_pwd):
                    if new_pwd == confirm_pwd and len(new_pwd) >= 6:
                        instructors_db[reset_user]["password"] = hash_pwd(new_pwd)
                        save_json(INSTRUCTORS_JSON, instructors_db)
                        st.success("Password updated successfully! You can now log in.")
                    else:
                        st.error("New passwords do not match or are shorter than 6 characters.")
                else:
                    st.error("Username or current password incorrect.")

    # --- B. GİRİŞ YAPILMIŞ ÖĞRETMEN PANELİ ---
    else:
        current_user = st.session_state.logged_in_user
        user_info = instructors_db[current_user]

        st.sidebar.markdown(f"👤 **Logged in:** {user_info['name']}")
        if st.sidebar.button("Logout"):
            st.session_state.logged_in_user = None
            st.rerun()

        # Panel Sekmeleri
        tab_setup, tab_analytics, tab_settings = st.tabs([
            "📚 Course & Requirement Setup", 
            "📊 Analytics Dashboard", 
            "⚙️ Account Settings & Management"
        ])

        # --- TAB 1: DERS VE MÜFREDAT YAPILANDIRMASI (CUSTOMIZABLE SYLLABUS) ---
        with tab_setup:
            st.subheader("🛠️ Course Management & Grading Requirements")
            st.write("Configure active courses, attendance mandates, assessment types, and weight distributions.")

            c1, c2 = st.columns([1, 2])
            with c1:
                course_code_inp = st.text_input("Course Code:", value="MCH2008").strip().upper()
                term_inp = st.selectbox("Academic Term:", ["Fall 2026", "Spring 2027", "Summer 2027"])
                
                st.markdown("##### 📋 Attendance Rules")
                att_mandatory = st.checkbox("Attendance Mandatory?", value=True)
                att_weight = st.number_input("Attendance Weight in Grade (%)", min_value=0, max_value=20, value=0)

            with c2:
                st.markdown("##### 📝 Evaluation Components (Requirements)")
                st.caption("Add or modify assessment components (Quizzes, Homeworks, Midterms, Projects, etc.).")

                existing_config = course_configs_db.get(course_code_inp, {})
                existing_assessments = existing_config.get("assessments", [
                    {"name": "Quiz", "count": 9, "best_of": 7, "total_weight": 20.0},
                    {"name": "Homework", "count": 5, "best_of": 5, "total_weight": 20.0},
                    {"name": "Midterm Exam", "count": 1, "best_of": 1, "total_weight": 25.0},
                    {"name": "Final Exam", "count": 1, "best_of": 1, "total_weight": 35.0}
                ])

                df_assessments = pd.DataFrame(existing_assessments)
                edited_df = st.data_editor(
                    df_assessments,
                    num_rows="dynamic",
                    column_config={
                        "name": st.column_config.TextColumn("Requirement Type", required=True),
                        "count": st.column_config.NumberColumn("Total Quantity", min_value=1, max_value=20, default=1),
                        "best_of": st.column_config.NumberColumn("Best N Counted", min_value=1, max_value=20, default=1),
                        "total_weight": st.column_config.NumberColumn("Total Weight (%)", min_value=0.0, max_value=100.0, default=10.0)
                    },
                    use_container_width=True
                )

                total_grade_weight = att_weight + edited_df["total_weight"].sum()
                if total_grade_weight == 100.0:
                    st.success(f"✅ Total Syllabus Weight: **{total_grade_weight:.1f}%** (Perfect)")
                else:
                    st.warning(f"⚠️ Total Syllabus Weight: **{total_grade_weight:.1f}%** (Should equal 100%)")

                if st.button("💾 Save Course Configuration", type="primary"):
                    course_configs_db[course_code_inp] = {
                        "instructor": current_user,
                        "term": term_inp,
                        "attendance_mandatory": att_mandatory,
                        "attendance_weight": att_weight,
                        "assessments": edited_df.to_dict(orient="records")
                    }
                    save_json(CONFIGS_JSON, course_configs_db)
                    st.success(f"Configuration saved for **{course_code_inp}**!")

        # --- TAB 2: ÖĞRETMEN ANALİTİK PANELİ ---
        with tab_analytics:
            st.subheader("📊 Instructor Analytics Dashboard")
            all_courses = sorted(list(set(df_db["course_code"].dropna().unique().tolist()).union(set(course_configs_db.keys()))))
            
            if all_courses:
                sel_c = st.selectbox("Select Course to Analyze:", all_courses)
                
                # MCH2008 Özel Ödev Özeti
                if sel_c == "MCH2008" and not df_hw.empty:
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

                course_data = df_db[df_db["course_code"] == sel_c]
                if not course_data.empty:
                    weeks = course_data["week"].dropna().unique().tolist()
                    sel_w = st.selectbox("Select Academic Week:", weeks)
                    w_data = course_data[course_data["week"] == sel_w]

                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Enrolled Students", len(w_data))
                    m2.metric("Attendance", f"{len(w_data[w_data['attendance'] == '+'])}")
                    q_scores = w_data['quiz_score_num'].dropna()
                    m3.metric("Quizzes Evaluated", len(q_scores))
                    m4.metric("Class Average", f"{q_scores.mean():.2f} / 100" if not q_scores.empty else "N/A")

                    st.divider()
                    st.markdown("#### 📋 Detailed Student Roster")
                    st.dataframe(w_data[["student_id", "full_name", "section", "attendance", "quiz_no", "quiz_score", "feedback"]], use_container_width=True)
                else:
                    st.info("No weekly records published for this course yet.")

        # --- TAB 3: HESAP AYARLARI VE YENİ İNSTRUCTOR EKLEME (YÖNTEM 1) ---
        with tab_settings:
            st.subheader("⚙️ Account Settings & Management")
            st.write(f"Logged in as: **{user_info['name']}** (`{user_info['email']}`)")

            # 1. Kendi Şifresini Değiştirme
            with st.expander("🔑 Change My Password"):
                p1 = st.text_input("Current Password:", type="password", key="s_old")
                p2 = st.text_input("New Password:", type="password", key="s_new")
                p3 = st.text_input("Confirm New Password:", type="password", key="s_conf")

                if st.button("Change Password"):
                    if user_info["password"] == hash_pwd(p1):
                        if p2 == p3 and len(p2) >= 6:
                            instructors_db[current_user]["password"] = hash_pwd(p2)
                            save_json(INSTRUCTORS_JSON, instructors_db)
                            st.success("Your password has been changed successfully!")
                        else:
                            st.error("Passwords do not match or are shorter than 6 characters.")
                    else:
                        st.error("Current password incorrect.")

            st.divider()

            # 2. Yeni Akademisyen Hesabı Oluşturma Formu (YÖNTEM 1)
            st.markdown("##### ➕ Register New Instructor")
            st.caption("Add a new faculty member to grant them course management and analytics access.")

            with st.form("add_instructor_form", clear_on_submit=True):
                new_name = st.text_input("Full Name (e.g. Dr. Ahmet Yılmaz)")
                new_email = st.text_input("Email Address (e.g. ahmet.yilmaz@bau.edu.tr)")
                new_user = st.text_input("Username (e.g. ahmetyilmaz)").strip().lower()
                new_pass = st.text_input("Temporary Password:", type="password")
                submit_inst = st.form_submit_button("Create Instructor Account", type="primary")

                if submit_inst:
                    if not new_user or not new_pass or not new_name:
                        st.error("Please fill in all required fields.")
                    elif new_user in instructors_db:
                        st.error(f"Username '{new_user}' is already registered!")
                    else:
                        # Yeni akademisyeni SHA-256 hash'li şifre ile veritabanına ekle
                        instructors_db[new_user] = {
                            "name": new_name,
                            "password": hash_pwd(new_pass),
                            "email": new_email
                        }
                        save_json(INSTRUCTORS_JSON, instructors_db)
                        st.success(f"🎉 Account successfully created for **{new_name}**! Username: `{new_user}`")

            st.divider()

            # 3. Mevcut Kayıtlı Akademisyen Listesi
            st.markdown("##### 👥 Registered Instructors List")
            registered_list = [
                {"Username": k, "Full Name": v["name"], "Email": v.get("email", "N/A")} 
                for k, v in instructors_db.items()
            ]
            st.dataframe(pd.DataFrame(registered_list), use_container_width=True)
