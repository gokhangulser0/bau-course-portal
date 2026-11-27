"""Local Desktop UI (runs on this computer only).

    run_desktop.bat                      (Windows, finds Python automatically)
    python -m streamlit run desktop_app.py --server.address 127.0.0.1 --server.port 8502

Course setup and evaluation criteria, roster import, Claude grading with cost estimates and budget
warnings, flagged-paper review, publishing, backups, term archives and the API balance ledger.
The public web portal (app.py) only serves the Student View and the Instructor Dashboard.
"""
import streamlit as st

from qs_core import config as cfgmod
from qs_core import excel_io as xio
from ui import common as ui
from ui import desktop

st.set_page_config(page_title="Course Operations - Local Desktop UI", page_icon="🖥️", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)
st.markdown("""<style>
  .block-container { max-width: 1280px; padding-top: 1.4rem; padding-bottom: 2rem; }
  .page-head h1 { font-size: 1.45rem; }
  .page-head p { font-size: 0.88rem; }
  h5 { margin-top: 0.6rem !important; margin-bottom: 0.2rem !important; }
  .metric-card { padding: 8px 10px; }
  .metric-card .value { font-size: 1.15rem; }
  .metric-grid { gap: 8px; margin: 4px 0 10px 0; }
  div[data-testid="stVerticalBlock"] { gap: 0.6rem; }
  section[data-testid="stSidebar"] .stRadio label { padding: 1px 0; }
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _bootstrap():
    cfgmod.ensure_file()
    try:
        xio.ensure_templates()
    except Exception:
        pass
    return True


_bootstrap()
desktop.render()
