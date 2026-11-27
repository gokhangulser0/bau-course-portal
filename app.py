"""BAU Mechatronics academic portal (Streamlit entry point).

    streamlit run app.py

Student View   public portal: announcements, New badges, standing, What-If, weekly log, class comparison
Instructor     password-protected dashboard (see ui/instructor.py)
"""
import streamlit as st

from qs_core import config as cfgmod
from qs_core import excel_io as xio
from ui import common as ui
from ui import instructor, student

st.set_page_config(page_title="Academic Portal - BAU Mechatronics", page_icon="🎓", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(ui.CSS, unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _bootstrap():
    """First run: create courses_config.json and the template workbooks if they are missing."""
    cfgmod.ensure_file()
    try:
        xio.ensure_templates()
    except Exception:
        pass
    return True


_bootstrap()

st.sidebar.title("Navigation")
mode = st.sidebar.radio("Go to:", ["Student View", "Instructor Dashboard"])
if mode == "Student View":
    student.render()
else:
    instructor.render()
