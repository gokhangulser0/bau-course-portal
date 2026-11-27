"""Background grading runs for the Local Desktop UI.

A grading run used to execute inside the Streamlit script: any click or page switch during the run stopped
it half-way (the tokens already spent were lost). Runs now execute in a background thread of the desktop
app's server process; the page only reads their progress. One run at a time.

    start(job, api_key)   -> run id (raises ValueError when another run is still active)
    current()             -> the active or most recent run (dict snapshot) or None
    stop(run_id)          -> ask the run to stop after the papers already sent
    is_running(queue_id)  -> True while that review queue is still being graded
"""
from __future__ import annotations

import threading
import time
import uuid

_LOCK = threading.Lock()
_RUNS: dict = {}
_LAST: list = []          # run ids, newest last


def _snapshot(r: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in r.items() if k not in ("thread", "cancel")}


def start(job, api_key: str) -> str:
    from . import grader
    with _LOCK:
        for r in _RUNS.values():
            if r["status"] == "running":
                raise ValueError("Another grading run is still in progress. Wait for it to finish or stop it.")
        rid = uuid.uuid4().hex[:10]
        run = {"id": rid, "status": "running", "label": f"{job.course} · Sec {job.section} · Week {job.week}",
               "started": time.time(), "finished": None, "done": 0, "total": 0, "stage": "Starting",
               "usage": {}, "lines": [], "queue_id": None, "error": None, "result": None,
               "cancel": threading.Event()}
        _RUNS[rid] = run
        _LAST.append(rid)

    def on_event(ev):
        with _LOCK:
            run["done"], run["total"] = ev["done"], ev["total"]
            stages = {"attendance": "Attendance", "paper": "Papers", "rubric": "Rubric", "start": "Starting"}
            run["stage"] = stages.get(ev["type"], ev["type"])
            run["usage"] = ev["usage"]
            run["lines"].append(ev["message"])
            del run["lines"][:-30]
            if ev.get("queue_id"):
                run["queue_id"] = ev["queue_id"]

    def work():
        try:
            q = grader.run_grading(job, api_key, on_event, cancel=run["cancel"])
            with _LOCK:
                run["result"] = {"id": q["id"], "status": q["status"], "papers": len(q["papers"]),
                                 "present": len(q["attendance"]["present"]), "usage": q.get("usage", {}),
                                 "notes": list(q.get("notes", [])), "open_flags": len(grader.open_flags(q)),
                                 "archived_pdfs": list(q.get("archived_pdfs") or [])}
                run["queue_id"] = q["id"]
                run["status"] = "done"
        except Exception as e:  # noqa: BLE001
            with _LOCK:
                run["error"] = str(e)
                run["status"] = "failed"
        finally:
            with _LOCK:
                run["finished"] = time.time()

    t = threading.Thread(target=work, name=f"grading-{rid}", daemon=True)
    run["thread"] = t
    t.start()
    return rid


def get(rid: str) -> dict | None:
    with _LOCK:
        r = _RUNS.get(rid)
        return _snapshot(r) if r else None


def current() -> dict | None:
    with _LOCK:
        for rid in reversed(_LAST):
            return _snapshot(_RUNS[rid])
    return None


def active() -> dict | None:
    r = current()
    return r if r and r["status"] == "running" else None


def stop(rid: str) -> None:
    with _LOCK:
        r = _RUNS.get(rid)
        if r and r["status"] == "running":
            r["cancel"].set()
            r["lines"].append("Stopping: the papers already sent finish, nothing new is sent.")


def is_running(queue_id: str) -> bool:
    with _LOCK:
        return any(r["status"] == "running" and r.get("queue_id") == queue_id for r in _RUNS.values())
