"""Shared Streamlit helpers: secrets, cached data loading, HTML cards, flash messages, publishing."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import portal_logic as pl
from qs_core import announcements as ann
from qs_core import config as cfgmod
from qs_core import excel_io as xio
from qs_core import gitops
from qs_core.common import ANNOUNCEMENTS_FILE, CONFIG_FILE, DB_FILE, META_FILE, STATUS_FILE, path
from portal_logic import safe_html as esc

CSS = """
<style>
    /* Hide the toolbar menu but keep the header: it holds the button that reopens a collapsed sidebar */
    [data-testid="stToolbar"] { visibility: hidden !important; }
    header[data-testid="stHeader"] { background: transparent !important; }
    #MainMenu { visibility: hidden !important; }
    footer { visibility: hidden !important; }

    .block-container { max-width: 1100px; padding-left: 1rem; padding-right: 1rem; }

    .metric-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 6px 0 14px 0; }
    .metric-card { position: relative; background: rgba(128,128,128,0.10); border: 1px solid rgba(128,128,128,0.28);
                   border-radius: 10px; padding: 12px; text-align: center; }
    .metric-card .label { font-size: 0.82rem; opacity: 0.75; }
    .metric-card .value { font-size: 1.4rem; font-weight: 700; line-height: 1.35; }
    .metric-card .sub { font-size: 0.8rem; opacity: 0.8; }

    .chip { display: inline-block; padding: 4px 10px; border-radius: 6px; font-weight: 600; font-size: 0.9rem; }
    .chip-present { background: rgba(40,167,69,0.18); border: 1px solid rgba(40,167,69,0.55); }
    .chip-absent { background: rgba(220,53,69,0.18); border: 1px solid rgba(220,53,69,0.55); }
    .chip-noclass { background: rgba(255,193,7,0.22); border: 1px solid rgba(255,193,7,0.65); }
    .chip-visit { background: rgba(23,162,184,0.18); border: 1px solid rgba(23,162,184,0.55); font-weight: 500; }

    .badge-new { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px; font-size: 0.68rem;
                 font-weight: 700; letter-spacing: 0.03em; text-transform: uppercase; vertical-align: middle;
                 color: #fff; background: #d63384; }
    .metric-card .badge-new { position: absolute; top: 6px; right: 6px; margin: 0; }

    .week-card { border: 1px solid rgba(128,128,128,0.28); border-radius: 12px; padding: 12px 14px;
                 margin-bottom: 12px; background: rgba(128,128,128,0.05); }
    .week-card .head { display: flex; justify-content: space-between; align-items: center;
                       flex-wrap: wrap; gap: 6px; font-weight: 700; font-size: 1.05rem; }
    .week-card .row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 18px; margin-top: 8px; }
    .week-card .score { font-size: 1.3rem; font-weight: 700; }
    .week-card .quiz-name { font-weight: 700; min-width: 64px; }
    .week-card .muted { opacity: 0.75; font-size: 0.9rem; }
    .quiz-row { margin-top: 10px; padding-top: 8px; border-top: 1px dashed rgba(128,128,128,0.35); }
    .week-card details { margin-top: 8px; }
    .week-card summary { cursor: pointer; font-weight: 600; }
    .fb { margin-top: 6px; padding: 10px 12px; border-left: 3px solid rgba(23,162,184,0.75);
          background: rgba(23,162,184,0.08); border-radius: 4px; line-height: 1.5; overflow-wrap: anywhere; }
    .note { margin-top: 8px; padding: 10px 12px; border-left: 3px solid rgba(255,193,7,0.85);
            background: rgba(255,193,7,0.10); border-radius: 4px; line-height: 1.5; overflow-wrap: anywhere; }

    .feed { border: 1px solid rgba(128,128,128,0.28); border-radius: 12px; padding: 6px 14px; margin: 8px 0 16px 0; }
    .feed-item { padding: 10px 0; border-bottom: 1px solid rgba(128,128,128,0.18); }
    .feed-item:last-child { border-bottom: none; }
    .feed-item .t { font-weight: 700; }
    .feed-item .m { font-size: 0.8rem; opacity: 0.7; margin-top: 2px; }
    .feed-item .b { margin-top: 4px; line-height: 1.5; overflow-wrap: anywhere; }
    .whatsnew { border: 1px solid rgba(214,51,132,0.45); background: rgba(214,51,132,0.06); border-radius: 10px;
                padding: 8px 14px; margin: 6px 0 14px 0; }
    .whatsnew li { margin: 2px 0; }

    /* Evaluation criteria */
    .crit-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; margin: 8px 0 16px 0; }
    .crit-card { border: 1px solid rgba(128,128,128,0.28); border-radius: 12px; padding: 14px 16px;
                 background: rgba(128,128,128,0.06); display: flex; flex-direction: column; gap: 6px; }
    .crit-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
    .crit-name { font-weight: 700; font-size: 1.02rem; }
    .crit-weight { font-size: 0.8rem; font-weight: 700; padding: 2px 9px; border-radius: 999px;
                   background: rgba(76,120,168,0.16); border: 1px solid rgba(76,120,168,0.45); white-space: nowrap; }
    .crit-weight.muted { background: transparent; opacity: 0.65; font-weight: 500; }
    .crit-value { font-size: 1.7rem; font-weight: 800; line-height: 1.2; }
    .crit-value small { font-size: 0.85rem; font-weight: 500; opacity: 0.65; }
    .crit-status { font-size: 0.82rem; font-weight: 600; }
    .crit-status.st-graded { color: #2e9e5b; }
    .crit-status.st-progress { color: #c58a00; }
    .crit-status.st-pending { opacity: 0.7; }
    .crit-status.st-missing { color: #d9534f; }
    .crit-rule { font-size: 0.78rem; opacity: 0.7; }
    .crit-bar { height: 7px; border-radius: 999px; background: rgba(128,128,128,0.22); overflow: hidden; margin-top: 4px; }
    .crit-bar > div { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #4c78a8, #59a14f); }
    .crit-pts { font-size: 0.78rem; opacity: 0.8; }
    .crit-chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 4px; }
    .item-chip { font-size: 0.74rem; padding: 2px 7px; border-radius: 6px; border: 1px solid rgba(128,128,128,0.35); }
    .item-chip.chip-done { background: rgba(89,161,79,0.14); border-color: rgba(89,161,79,0.5); }
    .item-chip.chip-miss { background: rgba(217,83,79,0.12); border-color: rgba(217,83,79,0.45); }
    .item-chip.chip-wait { opacity: 0.6; }

    .statusline { display: flex; flex-wrap: wrap; gap: 6px 22px; font-size: 0.86rem; padding: 7px 12px;
                  border: 1px solid rgba(128,128,128,0.25); border-radius: 8px; margin: 2px 0 10px 0;
                  background: rgba(128,128,128,0.05); }
    .statusline b { opacity: 0.7; font-weight: 600; margin-right: 4px; }
    .statusline.flagged { border-color: rgba(197,138,0,0.65); background: rgba(255,193,7,0.08); }

    /* Top navigation bar (horizontal radio) */
    .st-key-topnav div[role="radiogroup"] { display: flex; flex-wrap: wrap; gap: 4px 6px; }
    .st-key-topnav div[role="radiogroup"] > label { border: 1px solid rgba(128,128,128,0.3); border-radius: 8px;
        padding: 3px 12px 3px 8px; margin: 0 !important; background: rgba(128,128,128,0.05); }
    .st-key-topnav div[role="radiogroup"] > label:has(input:checked) { border-color: rgba(76,120,168,0.8);
        background: rgba(76,120,168,0.14); font-weight: 600; }

    /* Page and section headers */
    .page-head { margin: 0 0 6px 0; }
    .page-head h1 { font-size: 1.9rem; margin: 0; }
    .page-head p { margin: 2px 0 0 0; opacity: 0.75; }
    .section-label { font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.6;
                     font-weight: 700; margin: 18px 0 4px 0; }
</style>
"""


def get_secret(name, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


def github_settings() -> dict:
    return {"token": get_secret("GITHUB_TOKEN"), "repo": get_secret("GITHUB_REPO"),
            "branch": get_secret("GITHUB_BRANCH") or "main"}


def mtime(rel: str) -> float:
    p = path(rel)
    return os.path.getmtime(p) if os.path.exists(p) else 0.0


# ----------------------------------------------------------------------------- cached loaders
@st.cache_data(ttl=300, show_spinner=False)
def _load_config(_m):
    return cfgmod.load()


def config() -> dict:
    return _load_config(mtime(CONFIG_FILE))


@st.cache_data(ttl=300, show_spinner=False)
def _load_db(db_m, status_m):
    df_all = pl.load_database(path(DB_FILE))
    status = pl.load_status(path(STATUS_FILE))
    df_active, hidden = pl.apply_status(df_all, status)
    stats = pl.class_stats(df_active)
    return df_all, df_active, status, stats, hidden


def database():
    """(df_all, df_active, status_df, stats, hidden_rows)"""
    return _load_db(mtime(DB_FILE), mtime(STATUS_FILE))


@st.cache_data(ttl=300, show_spinner=False)
def _load_roster_sheet(_m):
    from qs_core import dbio
    return dbio.read_roster(path(DB_FILE))


def roster_sheet() -> pd.DataFrame:
    """Course rosters stored in grades_database.xlsx (sheet 'Roster')."""
    return _load_roster_sheet(mtime(DB_FILE))


@st.cache_data(ttl=300, show_spinner=False)
def _load_table(name, _m):
    return xio.read_table(name)


def table(name: str) -> pd.DataFrame:
    return _load_table(name, mtime(name))


def course_tables(course: dict) -> dict:
    return {comp["workbook"]: table(comp["workbook"]) for comp in cfgmod.workbook_components(course)}


@st.cache_data(ttl=120, show_spinner=False)
def _load_ann(_m):
    return ann.load()


def announcements() -> list[dict]:
    return _load_ann(mtime(ANNOUNCEMENTS_FILE))


def refresh():
    st.cache_data.clear()


# ----------------------------------------------------------------------------- HTML helpers
NEW_BADGE = '<span class="badge-new">New</span>'


def metric_card(label, value, sub="", new=False):
    sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    badge = NEW_BADGE if new else ""
    return (f'<div class="metric-card">{badge}<div class="label">{esc(label)}</div>'
            f'<div class="value">{esc(value)}</div>{sub_html}</div>')


def page_header(title: str, subtitle: str = ""):
    sub = f"<p>{esc(subtitle)}</p>" if subtitle else ""
    st.markdown(f'<div class="page-head"><h1>{esc(title)}</h1>{sub}</div>', unsafe_allow_html=True)


def section_label(text: str):
    st.markdown(f'<div class="section-label">{esc(text)}</div>', unsafe_allow_html=True)


def metric_grid(cards):
    if cards:
        st.markdown('<div class="metric-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def last_updated_caption():
    dt = pl.last_updated(path(DB_FILE), path(META_FILE))
    if dt:
        st.caption(f"Data last updated: {dt.strftime('%d %b %Y, %H:%M')} (Istanbul time)")


def flash(kind: str, text: str):
    st.session_state.setdefault("flash", []).append((kind, text))


def show_flash():
    for kind, text in st.session_state.pop("flash", []) or []:
        {"success": st.success, "warning": st.warning, "error": st.error}.get(kind, st.info)(text)


def mark_pending(*files):
    """Remember files saved in this session that still need publishing (used when there is no git clone)."""
    st.session_state.setdefault("pending_publish", set()).update(files)


def pending_files() -> set:
    return set(st.session_state.get("pending_publish", set()))


def publish_files(files, message: str) -> dict:
    """Shared commit/push. Only called from the pre-push preview after explicit confirmation."""
    res = gitops.publish(files, message, github=github_settings())
    if res["ok"]:
        left = pending_files() - set(files)
        st.session_state["pending_publish"] = left
    return res


def report_publish(res: dict, prefix: str = ""):
    text = (prefix + " " if prefix else "") + " ".join(res["log"])
    if not res["ok"]:
        flash("error", text)
    elif res["mode"] == "none":
        flash("warning", text)
    else:
        flash("success", text)
