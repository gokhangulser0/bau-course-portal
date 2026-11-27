"""Sync Workbooks from Database: grades_database.xlsx is the source of truth; the course workbooks follow it.
Shown on the desktop Overview (under the mismatch alert) and on Archive, Backups & Publish -> Workbook sync."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from qs_core import db_sync

from . import common as ui


def render(cfg, key: str = "dbs", show_status: bool = True):
    clear = st.session_state.get(f"{key}_clear", True)
    try:
        plan = db_sync.plan(cfg, clear_missing=clear)
    except Exception as e:  # noqa: BLE001
        st.warning(f"The workbook sync check could not run: {e}")
        return
    changes, skipped = plan["changes"], plan["skipped"]
    if show_status:
        if changes:
            st.markdown(f'<div class="statusline flagged"><span><b>Workbooks behind the database</b> '
                        f'{len(changes)} cell(s)</span><span>grades_database.xlsx is the source of truth; the course '
                        f'workbooks can be updated from it.</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="statusline"><span><b>Course workbooks</b> match grades_database.xlsx'
                        '</span></div>', unsafe_allow_html=True)
    if st.button("Sync Workbooks from Database", key=f"{key}_open", disabled=not changes,
                 help="Copies quiz scores, quiz feedback and attendance from grades_database.xlsx into the course "
                      "workbooks. The database itself is never changed. Nothing is written until you confirm."):
        st.session_state[f"{key}_show"] = True
    if skipped:
        with st.expander(f"Records that cannot be synced ({len(skipped)})"):
            st.dataframe(pd.DataFrame(skipped), use_container_width=True, hide_index=True)
    if not (changes and st.session_state.get(f"{key}_show")):
        return
    with st.container(border=True):
        st.markdown(f"**{len(changes)} workbook cell(s) will be updated from grades_database.xlsx**")
        st.dataframe(pd.DataFrame(db_sync.summarize(changes)), use_container_width=True, hide_index=True)
        with st.expander("Show every cell"):
            st.dataframe(pd.DataFrame(db_sync.public(changes)).astype(str), use_container_width=True,
                         hide_index=True)
        st.checkbox("Also clear workbook values that are empty or missing in the database", value=clear,
                    key=f"{key}_clear",
                    help="Only for quizzes and weeks that the database holds for that section. Untick to fill and "
                         "update values only.")
        st.caption(f"Files: {', '.join(db_sync.files_of(changes))}. Each workbook is checked for an open Excel "
                   f"window and gets a stamped backup first. Only cell values change: every section sheet and "
                   f"All Sections keep their row heights, column widths, borders, fonts, number formats and "
                   f"alignment. These workbooks are local only, so nothing needs to be pushed.")
        ok = st.checkbox("I checked the list. Update the workbooks from the database.", key=f"{key}_ok")
        c1, c2 = st.columns([1.3, 4])
        if c1.button(f"Write {len(changes)} cell(s)", type="primary", disabled=not ok, key=f"{key}_apply"):
            try:
                with st.spinner("Updating the workbooks (stamped backups first)..."):
                    out = db_sync.apply(cfg, changes)
                ui.mark_pending(*out["files"])
                ui.refresh()
                for k in ("show", "ok"):
                    st.session_state.pop(f"{key}_{k}", None)
                ui.flash("success", f"Workbooks updated from grades_database.xlsx: {out['cells']} cell(s). "
                         + " ".join(out["log"]))
            except ValueError as e:
                ui.flash("error", str(e))
            except Exception as e:  # noqa: BLE001
                ui.flash("error", f"Sync failed: {e}")
            st.rerun()
        if c2.button("Cancel", key=f"{key}_cancel"):
            st.session_state.pop(f"{key}_show", None)
            st.rerun()
