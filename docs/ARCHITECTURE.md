# Quiz System: Architecture & Upgrade Notes

## 1. Workspace inspection (Desktop/quiz_system_claude, 24 Nov 2026)

| File | Sheets | Header row (row 1) | Notes |
|---|---|---|---|
| `MCH2008_attendance.xlsx` | `901`, `902`, `All Sections` | Student ID, Full Name, Week #1…#9, Total Attendance (All Sections adds Section, Department) | Times New Roman 12, thin black borders, centred + wrap, all rows 39.95 pt, widths A 10.9 / B 38.9 / C 12.4. `Total Attendance` = `=COUNTIF(C2:K2,"+") & "/9"`. Pre-formatted empty rows down to row 87, a merged note cell (A84:C84), autofilter A1:C1. |
| `MCH2008_grades.xlsx` | `901`, `902`, `All Sections` | Student ID, Full Name, Department, Quiz #1, Quiz #1 FB … Quiz #9 FB | Header row 30 pt, data rows 39.95 pt, widths A 15 / B 37–44 / C 35 / D:U 13 (grouped column range). |
| `MCH2008_homework.xlsx` | `901`, `902`, `All Sections` | Student ID, Full Name, Department, HW #1…#5 | Same style as grades. All Sections HW #4/#5 cells are formulas mirroring the section sheets (`=IF('901'!F3="","",'901'!F3)`). |
| `MCH2008_midterm.xlsx` | `901`, `902`, `All Sections` | Section sheets: copied **homework** headers (no Midterm column). All Sections: Student ID, Midterm | The real midterm data lives only in All Sections (IDs stored as numbers, Calibri 11). The reader handles this; new midterm workbooks are generated with a proper `Midterm` column on every sheet. |
| `MCH2015_attendance.xlsx` | `901`, `902` | Same as MCH2008 attendance | No All Sections sheet. |
| `MCH2015_grades.xlsx` | `901`, `902`, `All Sections` | Quiz #1…#5 (+ FB) | |
| `grades_database.xlsx` | `Sheet1` | student_id, full_name, course_code, section, week, attendance, quiz_no, quiz_score, feedback, attended_section | 744 rows. MCH2008: 46 + 35 students, MCH2015: 36 + 39. |
| `class_status.csv` | | course_code, week_no, section, note, created_at | "No Class This Week" entries. |
| `input/` | | | `attendance/*.pdf` (JPEG page scans ~2000×2800 px), `all_quizzes_scanned/*.pdf` (one TIFF page per student, 3296×4672 px), `quiz_question.pdf`. |

No school class-roster export was found in the folder. The roster importer detects the header row on its
own and maps English or Turkish column names (Student ID / Öğrenci No, Full Name / Adı Soyadı or Adı +
Soyadı, Section / Şube, Department / Bölüm).

## 2. File plan

### Updated
| File | Change |
|---|---|
| `app.py` | Thin entry point: page config, CSS, bootstrap, router to `ui/student.py` / `ui/instructor.py`. |
| `portal_logic.py` | English; `COURSE_CONFIG` removed (rules now come from `courses_config.json`); adds `student_attendance()`. |
| `process_week.py` | CLI over the shared grading engine: `--course --section --week --quiz`, pre-flight estimate, threshold prompt, live usage, review queue, approval, pre-push summary. |
| `push_homework.py`, `push_midterm.py`, `manuel_push.py` | Removed in the November 2026 cleanup; use `publish.py` or the in-app pre-push preview. |
| `requirements.txt`, `.gitignore` | Adds pypdf, Pillow, anthropic, requests; ignores backups/, archives/, review_queue/, billing_ledger.json. |

### New
| File | Purpose |
|---|---|
| `courses_config.json` | Terms, courses, sections, weeks, components, weights, rules (best N / all / evaluated). |
| `announcements.json` | Announcements with scope All / Course / Course + sections. |
| `billing_ledger.json` | Local balance ledger + budget/token thresholds + price overrides (not published). |
| `templates/*.xlsx` | Header + one formatted row cloned from the MCH2008 base workbooks. |
| `qs_core/config.py` | Config schema, validation, workbook naming. |
| `qs_core/excel_io.py` | Template-preserving workbook generation, row insert/move/delete with formula translation, All Sections relinking, value writes, readers. |
| `qs_core/roster.py` | Roster parsing, diff (new / dropped / section change / name change), apply. |
| `qs_core/standing.py` | Generic standing, contributions and What-If. |
| `qs_core/grader.py` | Claude grading, flags, review queue, apply after approval. |
| `qs_core/billing.py` | Prices, image/PDF token estimation, cost estimate, thresholds, live tracker, ledger. |
| `qs_core/announcements.py`, `qs_core/badges.py` | Feed and New/unread badges (browser cookie per Student ID). |
| `qs_core/backup.py`, `qs_core/archive.py`, `qs_core/gitops.py` | Stamped backups/rollback, term zip, unified publish + pre-push diff. |
| `ui/*.py` | Student portal and the instructor dashboard tabs. |
| `publish.py` | CLI publish with pre-push summary. |

## 3. Data flow

```
roster export ──► Roster Sync ──► <CODE>_attendance / _grades / _<component>.xlsx  (template formatting)
scans + rubric ─► AI Grading (pre-flight → live run) ─► review_queue/<id>.json
                         Review & Publish (fix IDs/scores, resolve flags, approve)
                                    └─► attendance + grades workbooks, grades_database.xlsx
                                    └─► pre-push summary ─► git commit/push ─► Streamlit Cloud portal
every write ─► backups/<TERM>_<COURSE>_<YYYYMMDD_HHMMSS>_<file>
end of term ─► archives/<TERM>.zip
```

## 4. Behaviour changes to know about
* "Current Standing" base = weights of components that have **started** (MCH2008 now shows a 40% base until the
  first homework is graded; before it showed 60% with homework counted as 0).
* MCH2015 has no weights in the old code; it is configured with `show_standing: false` until weights are set in
  Course Setup.
* Push scripts publish data files only. Use `python publish.py --code` to push application code.

## 5. Update (24 Nov 2026): university roster export and LAN testing

* **Roster format** `Öğretim Elemanı Not Excel Raporu.xlsx` (sheet `Worksheet`) is recognised exactly:
  `Öğrenci Numarası` → Student ID (text), `Adı` + `Soyadı` → Full Name, `Program` → Department,
  `Ders Kodu` → course filter, `Section` or `Lab Section` → section sheets (chosen in Roster Sync and saved
  per course as `roster_section_column` in `courses_config.json`). `Student Program ID`, `Harf Notu`,
  `Bütünleme Harf Notu` and `Ders Kayıt Tarihi` are ignored. The generic matcher no longer maps
  `Student Program ID` to Department. Section sheets are ordered numerically (1, 4, 6, 12 … 901, 902).
* **LAN Test** tab (Instructor Dashboard): bind address, port, local IPv4 addresses with clickable URLs and a
  reachability check, the launch command, `.streamlit/config.toml` snippet and firewall tips.
  `run_lan.bat` starts the app in LAN mode on Windows.

## 6. Update (24 Nov 2026): semester import and Python-independent LAN launcher

* `qs_core/semester_import.py` + **Roster Sync → Semester Import** + `import_rosters.py` (CLI): reads every
  `Öğretim Elemanı Not Excel Raporu*.xlsx` (including `(1)`, `(2)` …) from the folder or `input/rosters/`,
  groups rows by `Ders Kodu`, creates new courses in `courses_config.json`, and generates/updates
  `<COURSE>_attendance.xlsx` and `<COURSE>_grades.xlsx` with one worksheet per section (sheet name = section)
  using the template formatting. Existing grades/attendance are kept.
* `grades_database.xlsx` gains a `Roster` sheet (current roster per course). All database writes go through
  `qs_core/dbio.py`, which keeps both sheets. The student portal recognises students from this sheet.
* `run_lan.ps1` (called by `run_lan.bat`) and `run_lan.sh` find a Python interpreter with Streamlit
  (.venv / venv / env, Conda, `py -3` and `py -0p`, PATH without the Microsoft Store stub, standard install
  folders), offer to create `.venv`, pick a free port, print the LAN URLs and start Streamlit on 0.0.0.0.
* `qs_core/pyenv.py` powers the Python section of the LAN Test tab (running interpreter, other interpreters,
  copyable commands that do not need `python` on PATH).

## 7. Update (24 Nov 2026): term archive, evaluation criteria, Local Desktop UI

* **Two apps.** `desktop_app.py` (Local Desktop UI, started with `run_desktop.bat`, bound to 127.0.0.1:8502):
  Overview, Courses & Criteria, Rosters (semester import + sync), AI Grading (balance, pre-flight estimate,
  thresholds, live counter), Review & Publish, Term Archive, Archive/Backups/Publish, Billing & Settings.
  `app.py` (web portal): Student View + Instructor Dashboard (Analytics, Announcements & No Class, LAN Test).
* **Term archive.** After every grading run the attendance, quiz and question files are merged/converted into
  `term_archive/<COURSE>/<course>_w<WW>_<section>_<attendance|quiz|question>.pdf` (index.json records the
  details; a second quiz in the same week/section gets `-q<N>`). Git-ignored; included in the term `.zip`.
* **Evaluation criteria.** Project, Quiz, Homework, Midterm and Final are standard criteria in Course Setup
  (used/weight/items/rule/best N). The student Overview always shows every configured criterion with its
  weight, score or status (graded / in progress / not published / missing) and contribution, also when the
  overall standing is hidden or a weight is not set yet.

## 8. Update (24 Nov 2026): Experiment criterion, term cleanup, compact UI

* **Experiment** is a standard criterion (items with a score and a feedback column per experiment:
  `Exp #1`, `Exp #1 FB`, … in `<CODE>_experiments.xlsx`). Claude can grade experiment handouts (AI Grading →
  What to grade → Experiment, or `process_week.py --experiment N`). Results go to the experiments workbook;
  attendance (if scanned) goes to the attendance workbook and database without touching quiz scores.
  Handouts are archived as `term_archive/<COURSE>/<course>_wNN_<section>_experiment.pdf`
  (`experiment-question.pdf` for the sheet; `-e<N>` when a second experiment shares the week/section).
  Students see every criterion card plus an expandable feedback list for Experiment.
* **Terms.** New terms are normalised (`2026-2027 Spring` → `2026-2027spring`) and validated; adding a term no
  longer switches the active term unless "Make active" is ticked. `qs_core/termclean.py` + Courses & Criteria →
  Terms → "Remove a term entered by mistake" removes a term and deletes backups/archives/review queues named after it.
* **UI.** Desktop pages use inner tabs (Courses & Criteria: Course and criteria / Workbooks / Terms; Rosters:
  Semester import / Roster sync; Archive: Publish / Semester archive / Backups; Billing: Balance / Thresholds /
  Model prices / History), a compact status line and tighter spacing. Decorative emojis were removed from both apps.

## 9. Update (24 Nov 2026): navigation fix, all assessments gradable, pre-push confirmation

* **Why only "Course Operations" showed:** the desktop page selector lived in the sidebar, and the shared CSS hid
  Streamlit's header, which holds the button that reopens a collapsed sidebar. On narrower windows Streamlit
  collapses the sidebar, so the other pages were unreachable. The desktop UI now has a top navigation bar, opens
  on AI Grading, and the header is no longer hidden (also in app.py, which starts with the sidebar expanded).
* **Grading** accepts any Quiz/Items/Single criterion (Quiz, Homework, Experiment, Project, Midterm, Final),
  a term filter, one or several sections per session (e.g. 901,902), and an inline balance popover
  (set / top up). Non-quiz results go to the criterion's workbook (score + "<column> FB").
  PDFs: `term_archive/<COURSE>/<course>_wNN_<sections>_<type>.pdf` (type = quiz, question, experiment,
  midterm, final, homework, project, and `<type>-question`).
* **Pre-push quick preview** (`ui/grading_tabs.show_pre_push`, `gitops.pre_push_summary`): files from
  `git status` (incl. untracked), the courses / weeks / sections / items each file affects, per-file details,
  other local changes not included, and a mandatory confirmation checkbox. Every publish in both apps goes
  through it (announcements, No Class, course setup, roster sync, semester import, grading results).
* **Missing entries** (`qs_core/coverage.py`, Instructor Dashboard → Analytics): per course and assessment
  column only - entered, missing, enrolled, coverage, status. No per-student detail.
* **Same-week quizzes:** every quiz of a week is listed in that week's card ("Week 7 · 2 quizzes"), each with
  its score, class average and feedback.

## 10. Update (24 Nov 2026): re-grade queue, section comparison, grade revision

* **Re-grade queue** (Review & Publish → "Re-grade selected papers", `grader.regrade`): pick papers, optionally
  a question/part to focus on and an extra instruction; only those papers are sent to Claude again (estimate shown
  first, live counter while running, cost recorded in `billing_ledger.json` and added to the run's usage). The
  previous result goes to the paper's history, flags are recomputed and must be resolved again, and a run that was
  already written goes back to "pending review". Each run now stores the rubric and each paper's own pages
  (`review_queue/<run>/<paper>.pdf`); older runs are rebuilt from `term_archive/`.
* **Section comparison** (`qs_core/sectioncmp.py`): one line per course with the quiz average, attendance rate and
  every workbook criterion average per section; a spread of at least 10 points (scores) or 15 percentage points
  (attendance) is marked as noticeable. Shown in the web Instructor Dashboard (Analytics) and the desktop Overview.
* **Grade & feedback revision** (`qs_core/revision.py`, `ui/revision_ui.py`; desktop page "Grade Revision" and web
  dashboard tab "Grade Revision"): course → section → item (Quiz #n, HW #n, Exp #n, Project, Midterm, Final) →
  student; shows the current score/feedback, saves with a stamped backup into the course workbook (values only,
  formatting untouched) and, for quizzes, the portal database; logged in `revision_log.json` (git-ignored);
  published only through the pre-push preview.


## 11. Cleanup (25 Nov 2026)

* **Removed / archived:** `push_homework.py`, `push_midterm.py`, `manuel_push.py` (use `publish.py` or the
  in-app pre-push preview) and `legacy/` (kept in `archives/legacy_scripts_v1.zip`), via the one-time
  `cleanup_workspace.py`.
* **Rosters:** Semester Import is the main flow; "Manual roster sync (other roster files)" is a collapsed toggle
  below it.
* **Web portal:** the Instructor Dashboard keeps Analytics and Announcements & No Class. LAN Test and Grade
  Revision moved to the Local Desktop UI (no student lists on the web). The LAN panel checks the web portal on
  port 8501.
* **Simplified:** no announcement priority levels; no "Other components (advanced)" editor (existing
  non-standard components are kept unchanged); no model-price editor (prices are constants in `billing.py`).
* **"New" badges** are time-based (7 days): `updated_at` column in `grades_database.xlsx` and
  `portal_updates.json` (published) for workbook criteria; no browser cookies.
* **Safety:** `common.ensure_writable` checks every file before multi-file writes (grading approval, roster sync,
  revisions, workbook generation, database writes) and stops with "<file> is open in Excel. Save and close it,
  then try again. Nothing was changed."
* **Consistency:** `qs_core/consistency.py` compares quiz scores/feedback and attendance between the course
  workbooks and `grades_database.xlsx`; the desktop Overview shows "Excel and database mismatch: N record(s)".
* `termclean.py` stays as a manual tool (Courses & Criteria → Terms); it never runs automatically.

## 12. Absent zeros and the feedback scope (25 Nov 2026)

* **Feedback scope.** Written feedback exists only for **Quiz** and **Experiment / Lab Report**
  (`config.FEEDBACK_CRITERIA`). Homework, Midterm, Final and Project never get a feedback column, the grading
  run drops the model's feedback for them, Review & Publish and Grade Revision hide the feedback field, and the
  student portal shows no feedback box for them.
* **New grading runs** (`grader.apply_queue`). For a Quiz or an Experiment (`config.ABSENT_ZERO_CRITERIA`),
  every student of the graded section who is absent that week and has no score receives `0` in the course
  workbook (section sheet and All Sections) and in `grades_database.xlsx`, with the feedback
  `Absent - 0 points.`. Absent means: not on this session's attendance sheet when it was read, otherwise
  marked `-` / `A` / `Absent` / `0` for that week in `<COURSE>_attendance.xlsx` or the database. A student
  marked present anywhere that week (another section's session) is not absent. A score already entered
  (e.g. a make-up) is never overwritten. Midterm, Final and Homework are never auto-zeroed. Review & Publish
  lists the students who will receive 0 before approval; the applied run and its pre-push preview list them
  again.
* **Backfill / re-check** (`qs_core/absent_zeros.py`). `plan(cfg)` finds absent records of held quizzes and
  experiments whose score is empty in the workbook and/or the database; `apply()` runs the open-Excel check
  for every file, makes stamped backups, writes only cell values (template formatting unchanged) and updates
  the database. The week of each quiz comes from the database; experiment weeks come from applied grading
  runs. Desktop: Overview -> **Sync Absent Zeros** (preview, confirm, then pre-push preview).
  Command line: `python sync_absent_zeros.py` (preview) / `python sync_absent_zeros.py --apply`.
* **First backfill (25 Nov 2026):** 251 records (MCH2008: 140, MCH2015: 111) in `MCH2008_grades.xlsx`,
  `MCH2015_grades.xlsx` and `grades_database.xlsx`; old "Absent from class." / "Absent." notes were replaced
  with "Absent - 0 points.".

## 13. grades_database.xlsx is the source of truth (25 Nov 2026)

Manual edits are made in `grades_database.xlsx`, never in the course workbooks. When the two disagree, the
workbooks follow the database (`qs_core/db_sync.py`); the database is only read.

| Database | Course workbook |
|---|---|
| `quiz_score` of `Quiz n` | `<COURSE>_grades.xlsx` -> `Quiz #n` (section sheet; All Sections values, not its formulas) |
| `feedback` of `Quiz n` | `<COURSE>_grades.xlsx` -> `Quiz #n FB` |
| `attendance` of `Week n` (`+` if present in any record) | `<COURSE>_attendance.xlsx` -> `Week #n` |

* A workbook value the database lacks is cleared only when the database holds that quiz / week for the
  student's section (option "Also clear workbook values ..."; `--keep-extra` on the command line turns it off).
* Students in the database but not in the workbook are listed as "cannot be synced" (run the roster sync).
* Every workbook: open-Excel check, stamped backup `<TERM>_<COURSE>_<YYYYMMDD_HHMMSS>_<file>`, value-only
  writes (template formatting unchanged). The workbooks are git-ignored, so nothing is pushed.
* Desktop: Overview (under the mismatch alert) and Archive, Backups & Publish -> Workbook sync ->
  **Sync Workbooks from Database** (preview, confirm, write). Command line: `python sync_workbooks.py [--apply]`.
* Grade Revision reads quiz scores and feedback from the database first.
* Sync Absent Zeros keeps the database's own note (e.g. "signature missing") when the database already has 0.

## 14. Faster, cheaper grading (27 Nov 2026)

Measured before the change (MCH2008 Week 1 Quiz 1, 35 papers, Sonnet 4.6): ~1,100 output tokens and ~23 s per
paper, $0.84 per run, 2 papers unparseable. Causes: the rubric's own "Output format" section made the model
write a text report before the JSON, a 17-item breakdown, sequential calls, runs cut off by page clicks.

* **Forced tool output** (`grader.grade_tool`, `ATTENDANCE_TOOL`, `grade_call`): the model can only answer
  through `submit_grade` / `submit_attendance`, so there is no preamble and no JSON parse error.
* **Breakdown per rubric part** (A, B, C, ...), not per sub-item. Flags and review work as before.
* **Rubric output-format sections are removed from the prompt** (`grader.strip_output_format`); `rubric.txt`
  itself is not changed. Scoring rules, solution and points stay.
* **Parallel papers**: `parallel_papers` (default 4, Billing & Settings -> Thresholds). API calls run in worker
  threads; results, the live counter and the cost/token caps are handled in one place. The SDK retries 429/529.
* **Background runs** (`qs_core/jobs.py`): a run continues when the page reruns or you switch pages; one run at
  a time; **Stop** keeps the papers graded so far. Runs saved as "running" that are no longer active (e.g.
  the app was closed) are shown as "stopped".
* **Same-scan warning**: every run stores the SHA-1 of its scans (`file_hashes`); grading the same PDF again
  (e.g. under another week or quiz number) needs an explicit confirmation.
* `temperature` is tried once per model per session.
