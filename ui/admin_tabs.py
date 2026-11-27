"""Archive, Backups & Publish tab and Billing & Settings tab."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from qs_core import archive as arch
from qs_core import backup as bk
from qs_core import billing
from qs_core import gitops
from qs_core.common import path

from . import common as ui
from .grading_tabs import show_pre_push


def archive_tab(cfg):
    ui.page_header("Archive, Backups & Publish", "Publish portal data, pack the semester into a .zip, and roll "
                   "files back from stamped backups.")
    t1, t4, t2, t3 = st.tabs(["Publish", "Workbook sync", "Semester archive", "Backups"])
    with t1:
        _publish_panel(cfg)
    with t4:
        from . import db_sync_ui
        st.caption("grades_database.xlsx is the source of truth for quiz scores, quiz feedback and attendance. "
                   "After a manual edit there, update the course workbooks (`<COURSE>_grades.xlsx`, "
                   "`<COURSE>_attendance.xlsx`) from it here. The database itself is never changed by this sync.")
        db_sync_ui.render(cfg, key="dbs_admin")
    with t2:
        _archive_panel(cfg)
    with t3:
        _backups_panel(cfg)


def _publish_panel(cfg):
    # ---------------------------------------------------------------- publish status
    files = gitops.publishable_files(cfg)
    if gitops.repo_available():
        st.caption(f"Git clone detected (branch `{gitops.current_branch()}`). Portal data files: " + ", ".join(files))
    elif ui.github_settings().get("token"):
        st.caption("Publishing through the GitHub API (Streamlit secrets).")
    else:
        st.caption("No git clone and no GitHub secrets: changes stay on this machine.")
    show_pre_push(files, "Update portal data", key="admin")



def _archive_panel(cfg):
    # ---------------------------------------------------------------- archive
    st.caption("Pack every course-coded workbook of a term, the portal database, configuration, announcements, "
             "templates and (optionally) the scanned inputs and review queues into one `.zip` named after the term. "
             "Originals are not deleted.")
    terms = cfg["terms"] or [cfg.get("active_term", "")]
    a1, a2, a3 = st.columns([2, 1, 1])
    term = a1.selectbox("Term to archive:", terms, index=terms.index(cfg["active_term"]) if cfg["active_term"] in terms else 0)
    inc_in = a2.checkbox("Include input/ scans", value=True)
    inc_rv = a3.checkbox("Include review queues", value=True)
    files_t = arch.files_for_term(term, inc_in, inc_rv)
    total = sum(os.path.getsize(path(f)) for f in files_t)
    with st.expander(f"Files that will be included ({len(files_t)} files, {total / 1e6:.1f} MB)"):
        st.dataframe(pd.DataFrame({"File": files_t, "Size (KB)": [round(os.path.getsize(path(f)) / 1024) for f in files_t]}),
                     use_container_width=True, hide_index=True)
    if os.path.exists(path("archives", f"{term}.zip")):
        st.info(f"archives/{term}.zip already exists; it will be backed up and replaced.")
    if st.button(f"Create {term}.zip", type="primary", disabled=not files_t):
        try:
            res = arch.make_archive(term, inc_in, inc_rv)
            ui.flash("success", f"Archive created: {bk.relative(res['path'])} · {res['file_count']} files · "
                                f"{res['size_bytes'] / 1e6:.1f} MB.")
        except Exception as e:  # noqa: BLE001
            ui.flash("error", f"Archiving failed: {e}")
        st.rerun()
    for a in arch.list_archives():
        with open(a["path"], "rb") as f:
            st.download_button(f"Download {a['name']} ({a['size_bytes'] / 1e6:.1f} MB)", f.read(),
                               file_name=a["name"], mime="application/zip", key=f"dl_{a['name']}")



def _backups_panel(cfg):
    # ---------------------------------------------------------------- backups
    st.caption("A backup named `<TERM>_<COURSE>_<YYYYMMDD_HHMMSS>_<file>` is made automatically before every write. "
             "Restoring a backup first backs up the current version, so a rollback can itself be undone.")
    items = bk.list_backups()
    if not items:
        st.caption("No backups yet.")
        return
    originals = sorted({b.original for b in items})
    pick = st.selectbox("File:", ["(all files)"] + originals)
    shown = [b for b in items if pick == "(all files)" or b.original == pick]
    st.dataframe(pd.DataFrame([{"Backup": b.name, "Original file": b.original, "Term": b.term, "Course": b.course,
                                "Time": b.pretty_time, "Size (KB)": round(b.size / 1024)} for b in shown[:200]]),
                 use_container_width=True, hide_index=True)
    sel = st.selectbox("Backup to restore:", [b.name for b in shown[:200]])
    ok = st.checkbox(f"Replace the current file with this backup")
    if st.button("Restore backup", disabled=not ok):
        try:
            target, safety = bk.restore(sel)
            ui.refresh()
            ui.flash("success", f"Restored {bk.relative(target)}. The previous version was saved as "
                                f"{os.path.basename(safety) if safety else '(nothing to save)'}. Publish the file "
                                f"if the portal should show the restored data.")
        except Exception as e:  # noqa: BLE001
            ui.flash("error", f"Restore failed: {e}")
        st.rerun()


def billing_tab(cfg):
    ui.page_header("Billing & Settings", "Local Claude API balance ledger and budget thresholds. Model prices are "
                   "set in qs_core/billing.py.")
    led = billing.load_ledger()
    s = led["settings"]
    t_bal, t_thr, t_his = st.tabs(["Balance", "Thresholds", "History"])
    with t_bal:
        m1, m2, m3 = st.columns(3)
        m1.metric("Estimated remaining balance", f"${led['balance_usd']:.2f}")
        m2.metric("Total spent (recorded runs)", f"${led['total_spent_usd']:.2f}")
        m3.metric("Balance last set", (led.get("balance_set_at") or "—")[:16].replace("T", " "))
        st.caption("The ledger is local (billing_ledger.json, not published). Grading runs subtract their measured cost. "
                   "Set the balance from the Anthropic Console now and then to keep it exact.")
        b1, b2 = st.columns(2)
        with b1.form("set_balance"):
            amt = st.number_input("Set balance to (USD):", min_value=0.0, value=float(max(led["balance_usd"], 0)), step=1.0)
            note = st.text_input("Note:", placeholder="e.g. Checked in the Anthropic Console")
            if st.form_submit_button("Set balance"):
                billing.save_ledger(billing.set_balance(led, amt, note))
                st.rerun()
        with b2.form("top_up"):
            amt2 = st.number_input("Top up (USD):", min_value=0.0, value=10.0, step=1.0)
            note2 = st.text_input("Note:", key="tu_note", placeholder="e.g. Credit purchase")
            if st.form_submit_button("Add top-up"):
                billing.save_ledger(billing.top_up(led, amt2, note2))
                st.rerun()
    with t_thr, st.form("thresholds"):
        t1, t2, t3, t4 = st.columns(4)
        warn = t1.number_input("Warn when a run uses ≥ this % of the balance:", 1.0, 100.0, float(s["warn_pct_of_balance"]), 1.0)
        cap = t2.number_input("Per-run cost cap (USD, 0 = none):", 0.0, 500.0, float(s["run_cost_cap_usd"]), 0.5)
        tcap = t3.number_input("Per-run token cap (0 = none):", 0, 100_000_000, int(s["run_token_cap"]), 100_000)
        lowc = t4.number_input("Flag papers below model confidence:", 0.0, 1.0, float(s["low_confidence"]), 0.05)
        models = list(billing.MODELS)
        m1, m2 = st.columns([3, 1])
        model = m1.selectbox("Default model:", models, index=models.index(s["model"]) if s["model"] in models else 0,
                             format_func=lambda m: f"{billing.MODELS[m]['label']} ({m})")
        par = m2.number_input("Papers graded at the same time:", 1, 8, int(s.get("parallel_papers") or 4), 1,
                              help="Higher is faster. Lower it to 1-2 if runs report rate-limit (429) errors.")
        if st.form_submit_button("Save thresholds"):
            s.update({"warn_pct_of_balance": warn, "run_cost_cap_usd": cap, "run_token_cap": int(tcap),
                      "low_confidence": lowc, "model": model, "parallel_papers": int(par)})
            billing.save_ledger(led)
            st.rerun()

    with t_his:
        if led["history"]:
            hist = pd.DataFrame(led["history"][::-1])
            hist = hist.rename(columns={"at": "Time", "type": "Type", "amount_usd": "Amount (USD)", "note": "Note",
                                        "model": "Model", "calls": "Calls", "input_tokens": "Input tokens",
                                        "output_tokens": "Output tokens", "cache_read_tokens": "Cache read",
                                        "cache_write_tokens": "Cache write", "previous_usd": "Previous balance"})
            hist["Type"] = hist["Type"].map({"set_balance": "Balance set", "top_up": "Top-up",
                                             "grading_run": "Grading run"}).fillna(hist["Type"])
            st.dataframe(hist, use_container_width=True, hide_index=True)
        if not led["history"]:
            st.caption("No entries yet.")
