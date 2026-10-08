import streamlit as st
import pandas as pd
import numpy as np
import os

st.set_page_config(
    page_title="Academic Portal - BAU Mechatronics", 
    page_icon="🎓", 
    layout="wide"
)

# --- CSS Görsel Düzenlemeleri ---
st.markdown("""
<style>
    .present-tag { color: #28a745; font-weight: bold; font-size: 1.1rem; }
    .absent-tag { color: #dc3545; font-weight: bold; font-size: 1.1rem; }
</style>
""", unsafe_allow_html=True)

DB_FILE = "grades_database.xlsx"

# Öğretmen Paneli Şifresi
INSTRUCTOR_PASSWORD = "S6s2kTa9Rm"

if not os.path.exists(DB_FILE):
    st.info("No records published yet. Please check back later.")
    st.stop()

@st.cache_data(ttl=15)
def load_data():
    df = pd.read_excel(DB_FILE)
    df["quiz_score_num"] = pd.to_numeric(df["quiz_score"], errors="coerce")
    return df

df = load_data()

# --- Sol Menü (Navigasyon) ---
st.sidebar.title("Navigation")
portal_mode = st.sidebar.radio("Go to:", ["Student View", "Instructor Dashboard"])

# =========================================================================
# 1. ÖĞRENCİ PORTALI (STUDENT VIEW)
# =========================================================================
if portal_mode == "Student View":
    st.title("🎓 Student Academic Portal")
    st.write("Enter your **Student ID** to view attendance records, quiz feedback, and overall class performance.")

    student_id_input = st.text_input("Student ID:", placeholder="e.g. 21012345").strip()

    if student_id_input:
        matched = df[df["student_id"].astype(str) == student_id_input]
        if matched.empty:
            st.error("No record found matching this Student ID. Please ensure your ID is entered correctly.")
        else:
            student_name = matched.iloc[0]["full_name"]
            st.success(f"Welcome, **{student_name}**!")

            courses = matched["course_code"].unique().tolist()
            selected_course = st.selectbox("Select Enrolled Course:", courses)
            course_data = matched[matched["course_code"] == selected_course].copy()

            st.markdown(f"### Academic Status: `{selected_course}`")
            st.divider()

            for _, row in course_data.iterrows():
                week = row["week"]
                att = row["attendance"]
                q_label = row["quiz_no"]
                score = row["quiz_score"]
                feedback = row["feedback"]

                # O haftanın sınıf ortalamasını hesapla
                week_all = df