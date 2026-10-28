"""
Portal iş mantığı (Streamlit'ten bağımsız, test edilebilir).
app.py bu modülü import eder; ikisi de repo kökünde aynı klasörde durmalıdır.
"""
from __future__ import annotations

import base64
import html
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

LOCAL_TZ = "Europe/Istanbul"


def _zone(name: str = LOCAL_TZ):
    """Saat dilimi. Windows'ta tzdata yoksa Türkiye için sabit UTC+3'e düşer (2016'dan beri yaz saati yok)."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=3))


STATUS_COLUMNS = ["course_code", "week_no", "section", "note", "created_at"]
MIN_CLASS_FOR_CHART = 5      # bundan az öğrenci notu olan haftada dağılım grafiği gösterilmez (gizlilik)

# Ders bazlı not hesabı ayarları. Yeni ders için buraya ekle.
COURSE_CONFIG = {
    "MCH2008": {
        "total_quizzes": 9,    # dönem boyunca yapılacak toplam quiz (hafta iptali bunu DEĞİŞTİRMEZ,
                               # bir derste iki quiz de yapılabilir)
        "best_quizzes": 7,     # en yüksek kaç quiz sayılır
        "quiz_pts": 20.0,      # bu en iyi quiz'lerin finale toplam katkısı (puan)
        "hw_pts": 20.0,        # ödevlerin toplam katkısı (puan)
        "hw_count": 5,
    },
}


# ----------------------------------------------------------------------------- temizlik / biçim
def clean_id_str(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)) or val is pd.NA:
        return ""
    s = str(val).split(".")[0].strip()
    return "".join(ch for ch in s if ch.isdigit())


def clean_section(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)) or val is pd.NA:
        return ""
    s = str(val).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return "" if s.lower() in ("nan", "none", "<na>") else s


def week_number(label):
    m = re.search(r"\d+", str(label))
    return int(m.group()) if m else None


def safe_html(text) -> str:
    """Kullanıcı/LLM metnini HTML kutusu içinde güvenle göstermek için kaçırır.
    Böylece 'N*m' gibi ifadeler italik olmaz, '$' LaTeX'e dönmez, '<' etiket sayılmaz."""
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return ""
    s = html.escape(str(text).strip(), quote=True)
    s = s.replace("$", "&#36;")
    s = s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    return s


def half_up(x: float) -> int:
    """77.5 -> 78 (Python'un round'u 76.5 -> 76 yapar; öğrenci açısından yarım puan yukarı yuvarlanır)."""
    return int(math.floor(float(x) + 0.5))


def fmt_num(x, decimals: int = 1) -> str:
    """Tam sayıysa tam sayı, değilse ondalıklı göster. Asla kesme (truncate) yapmaz."""
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.{decimals}f}"


# ----------------------------------------------------------------------------- veri yükleme
def load_database(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_excel(path)
    if df.empty:
        return df
    df["quiz_score_num"] = pd.to_numeric(df["quiz_score"], errors="coerce")
    df["student_id"] = df["student_id"].apply(clean_id_str)
    df["section"] = df["section"].apply(clean_section)
    df["course_code"] = df["course_code"].astype(str).str.strip()
    df["week_no"] = pd.to_numeric(df["week"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    df["quiz_n"] = pd.to_numeric(df["quiz_no"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")  # "No Quiz" -> NaN
    if "attended_section" not in df.columns:
        df["attended_section"] = ""
    df["attended_section"] = df["attended_section"].apply(clean_section)
    return _dedupe(df)


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Satır kimliği = (ders, öğrenci, hafta, quiz). Bir haftada birden fazla quiz satırı olabilir.
    * Aynı haftada gerçek bir quiz satırı varsa eski 'No Quiz' satırı gereksizdir, atılır.
    * Aynı quiz için tekrar eden satırlarda notu olan ve '+' olan tercih edilir."""
    d = df.assign(_wk=df["week_no"].fillna(-1), _has_q=df["quiz_n"].notna(),
                  _has_score=df["quiz_score_num"].notna(),
                  _pres=df["attendance"].astype(str).str.strip().eq("+"), _i=range(len(df)))
    has_q_week = d.groupby(["course_code", "student_id", "_wk"])["_has_q"].transform("any")
    d = d[~((~d["_has_q"]) & has_q_week)]
    d = (d.sort_values(["_has_score", "_pres", "_i"])
          .drop_duplicates(["course_code", "student_id", "_wk", "quiz_n"], keep="last")
          .sort_values("_i"))
    return d.drop(columns=["_wk", "_has_q", "_has_score", "_pres", "_i"]).reset_index(drop=True)


def empty_status() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in STATUS_COLUMNS}).astype({"week_no": "int64"})


def load_status(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return empty_status()
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    except Exception:
        return empty_status()
    for c in STATUS_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df["course_code"] = df["course_code"].str.strip()
    df["section"] = df["section"].map(clean_section)
    df["week_no"] = pd.to_numeric(df["week_no"], errors="coerce")
    df = df.dropna(subset=["week_no"])
    df["week_no"] = df["week_no"].astype(int)
    return df[STATUS_COLUMNS].reset_index(drop=True)


def apply_status(df: pd.DataFrame, status: pd.DataFrame):
    """'No Class' işaretli (ders, hafta, şube) satırlarını veritabanından çıkarır. (aktif_df, gizlenen_satır_sayısı)"""
    if df.empty or status.empty:
        return df, 0
    keys = set(zip(status["course_code"], status["week_no"], status["section"]))
    mask = pd.Series(
        [(c, int(w), s) in keys if pd.notna(w) else False
         for c, w, s in zip(df["course_code"], df["week_no"], df["section"])],
        index=df.index,
    )
    return df[~mask].copy(), int(mask.sum())


def class_stats(df: pd.DataFrame) -> dict:
    """(ders, quiz_no) -> ortalama, medyan, öğrenci sayısı, notlar, hafta. Tek seferde groupby ile hesaplanır.
    Anahtar hafta değil quiz numarasıdır; böylece bir haftada iki quiz olabilir."""
    out = {}
    if df.empty:
        return out
    s = df[df["quiz_score_num"].notna() & df["quiz_n"].notna()]
    for (course, q), g in s.groupby(["course_code", "quiz_n"]):
        v = g["quiz_score_num"].to_numpy(dtype=float)
        wk = g["week_no"].dropna()
        out[(course, int(q))] = {"mean": float(v.mean()), "median": float(np.median(v)), "n": int(len(v)),
                                 "scores": v, "week_no": int(wk.min()) if len(wk) else None}
    return out


def week_attendance(df: pd.DataFrame) -> pd.DataFrame:
    """Haftada iki quiz satırı olsa bile her öğrenci o hafta için TEK yoklama kaydı sayılır ('+' varsa '+')."""
    if df.empty:
        return df
    d = df.assign(_pres=df["attendance"].astype(str).str.strip().eq("+"))
    out = (d.groupby(["course_code", "student_id", "week_no"], dropna=False)
             .agg(full_name=("full_name", "first"), section=("section", "first"), _pres=("_pres", "any"))
             .reset_index())
    out["attendance"] = out["_pres"].map({True: "+", False: "-"})
    return out.drop(columns="_pres")


def student_quiz_scores(rows: pd.DataFrame) -> dict:
    """{quiz_no: puan} - her quiz bir kez sayılır."""
    out = {}
    if rows.empty:
        return out
    for q, s in zip(rows["quiz_n"], rows["quiz_score_num"]):
        if pd.notna(q) and pd.notna(s):
            out[int(q)] = max(out.get(int(q), float("-inf")), float(s))
    return out


def held_quizzes(df: pd.DataFrame, course: str, section: str) -> set:
    """Bu ders+şubede şimdiye kadar yapılmış (kaydı girilmiş) quiz numaraları."""
    if df.empty:
        return set()
    m = (df["course_code"] == course) & df["quiz_n"].notna()
    if section:
        m &= (df["section"] == section)
    return {int(q) for q in df.loc[m, "quiz_n"]}


def student_section(rows: pd.DataFrame) -> str:
    secs = [s for s in rows["section"] if s] if not rows.empty else []
    return Counter(secs).most_common(1)[0][0] if secs else ""


# ----------------------------------------------------------------------------- zaman çizelgesi
def build_timeline(rows: pd.DataFrame, status_rows: pd.DataFrame, newest_first: bool = True) -> list:
    """Haftalık kartlar. Bir haftada birden fazla quiz varsa tek kartta birden fazla quiz satırı olur."""
    items = []
    if not rows.empty:
        for wk, g in rows.groupby(rows["week_no"].fillna(-1), sort=False):
            recs = g.to_dict("records")
            pres = [r for r in recs if str(r.get("attendance")).strip() == "+"]
            attended = next((str(r.get("attended_section") or "").strip() for r in pres
                             if str(r.get("attended_section") or "").strip()), "")
            quizzes = sorted([r for r in recs if pd.notna(r.get("quiz_n")) and pd.notna(r.get("quiz_score_num"))],
                             key=lambda r: r["quiz_n"])
            items.append({"type": "class", "week_no": int(wk) if wk != -1 else None,
                          "label": str(recs[0].get("week")), "attendance": "+" if pres else "-",
                          "attended_section": attended, "quizzes": quizzes})
    for rec in (status_rows.to_dict("records") if not status_rows.empty else []):
        items.append({"type": "no_class", "week_no": int(rec["week_no"]),
                      "label": f"Week {int(rec['week_no'])}", "row": rec})
    numbered = sorted([i for i in items if i["week_no"] is not None], key=lambda i: i["week_no"],
                      reverse=newest_first)
    unnumbered = [i for i in items if i["week_no"] is None]
    return numbered + unnumbered


# ----------------------------------------------------------------------------- not hesabı / what-if
def quiz_points(scores, best: int, pts: float) -> float:
    top = sorted((float(s) for s in scores), reverse=True)[:best]
    return sum(top) * pts / best / 100.0


def remaining_quizzes(cfg: dict, held_quizzes) -> int:
    """Kalan quiz = toplam quiz - şimdiye kadar yapılan quiz. Hafta iptalleri toplamı etkilemez."""
    return max(0, cfg["total_quizzes"] - len(set(int(q) for q in held_quizzes)))


def what_if(existing_scores, remaining: int, future_avg: float, cfg: dict) -> dict:
    best, pts = cfg["best_quizzes"], cfg["quiz_pts"]
    existing = list(existing_scores)
    return {
        "current": quiz_points(existing, best, pts),
        "projected": quiz_points(existing + [float(future_avg)] * remaining, best, pts),
        "max": quiz_points(existing + [100.0] * remaining, best, pts),
    }


def trend_frame(rows: pd.DataFrame, stats: dict, course: str) -> pd.DataFrame:
    recs = []
    for q, score in sorted(student_quiz_scores(rows).items()):
        recs.append({"quiz_n": q, "series": "You", "score": float(score)})
        st = stats.get((course, q))
        if st:
            recs.append({"quiz_n": q, "series": "Class average", "score": round(st["mean"], 1)})
    out = pd.DataFrame(recs, columns=["quiz_n", "series", "score"])
    return out.sort_values(["quiz_n", "series"]).reset_index(drop=True)


def bin_index(score: float, width: int = 10) -> int:
    return int(min(max(score, 0) // width, 100 // width - 1))


def histogram(scores, width: int = 10) -> pd.DataFrame:
    n_bins = 100 // width
    counts = np.zeros(n_bins, dtype=int)
    for s in scores:
        counts[bin_index(float(s), width)] += 1
    labels = [f"{i * width}-{i * width + width - 1}" for i in range(n_bins)]
    labels[-1] = f"{(n_bins - 1) * width}-100"
    return pd.DataFrame({"bin": labels, "count": counts, "start": [i * width for i in range(n_bins)]})


# ----------------------------------------------------------------------------- zaman damgası
def last_updated(db_path: str, meta_path: str | None = None, tz: str = LOCAL_TZ):
    zone = _zone(tz)
    if meta_path and os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                dt = datetime.fromisoformat(json.load(f)["db_updated"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=zone)
            return dt.astimezone(zone)
        except Exception:
            pass
    if os.path.exists(db_path):
        return datetime.fromtimestamp(os.path.getmtime(db_path), tz=zone)
    return None


# ----------------------------------------------------------------------------- "No Class" kayıtları
def add_status(status: pd.DataFrame, course: str, week_no: int, sections, note: str, now=None) -> pd.DataFrame:
    now = now or datetime.now(_zone())
    new = pd.DataFrame([{
        "course_code": course, "week_no": int(week_no), "section": clean_section(s),
        "note": (note or "").strip(), "created_at": now.isoformat(timespec="seconds"),
    } for s in sections], columns=STATUS_COLUMNS)
    keys = set(zip(new["course_code"], new["week_no"], new["section"]))
    keep = status[[(c, w, s) not in keys for c, w, s in
                   zip(status["course_code"], status["week_no"], status["section"])]]
    out = pd.concat([keep, new], ignore_index=True) if len(keep) else new
    return out.sort_values(["course_code", "week_no", "section"]).reset_index(drop=True)


def remove_status(status: pd.DataFrame, keys) -> pd.DataFrame:
    keys = set(keys)
    keep = [(c, int(w), s) not in keys for c, w, s in
            zip(status["course_code"], status["week_no"], status["section"])]
    return status[keep].reset_index(drop=True)


def status_to_csv_bytes(status: pd.DataFrame) -> bytes:
    return status[STATUS_COLUMNS].to_csv(index=False).encode("utf-8")


def write_status(path: str, status: pd.DataFrame) -> None:
    with open(path, "wb") as f:
        f.write(status_to_csv_bytes(status))


def github_put_file(token: str, repo: str, path: str, content: bytes, message: str,
                    branch: str = "main", timeout: int = 20) -> None:
    """GitHub Contents API ile dosyayı repoya commit eder (yoksa oluşturur, varsa günceller)."""
    import requests
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers, params={"ref": branch}, timeout=timeout)
    if r.status_code == 200:
        sha = r.json().get("sha")
    elif r.status_code == 404:
        sha = None
    else:
        raise RuntimeError(f"GitHub okuma hatası {r.status_code}: {r.text[:200]}")
    payload = {"message": message, "content": base64.b64encode(content).decode("ascii"), "branch": branch}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub yazma hatası {r.status_code}: {r.text[:200]}")