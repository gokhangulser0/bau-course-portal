"""Claude API pricing, pre-flight token/cost estimation, budget thresholds, live usage tracking
and the local balance ledger (billing_ledger.json).

Prices are USD per million tokens (Claude pricing page, November 2026). They can be overridden
in billing_ledger.json -> settings.price_overrides when Anthropic changes them.
"""
from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass, field

from .common import LEDGER_FILE, now_iso, read_json, write_json

# model id -> label, prices, image tier
MODELS = {
    "claude-sonnet-4-6": {"label": "Claude Sonnet 4.6", "input": 3.00, "cache_write": 3.75, "cache_read": 0.30,
                          "output": 15.00, "tier": "standard"},
    "claude-sonnet-5-5": {"label": "Claude Sonnet 5.5", "input": 2.00, "cache_write": 2.50, "cache_read": 0.10,
                          "output": 10.00, "tier": "high"},
    "claude-opus-5-5": {"label": "Claude Opus 5.5", "input": 4.00, "cache_write": 5.00, "cache_read": 0.20,
                        "output": 20.00, "tier": "high"},
    "claude-haiku-5-5": {"label": "Claude Haiku 5.5", "input": 0.10, "cache_write": 0.125, "cache_read": 0.01,
                         "output": 0.50, "tier": "high"},
    "claude-fable-5-2": {"label": "Claude Fable 5.2", "input": 8.00, "cache_write": 10.00, "cache_read": 0.20,
                         "output": 40.00, "tier": "high"},
    "claude-opus-4-6": {"label": "Claude Opus 4.6", "input": 5.00, "cache_write": 6.25, "cache_read": 0.50,
                        "output": 25.00, "tier": "standard"},
    "claude-haiku-4-5": {"label": "Claude Haiku 4.5", "input": 1.00, "cache_write": 1.25, "cache_read": 0.10,
                         "output": 5.00, "tier": "standard"},
}
DEFAULT_MODEL = "claude-sonnet-4-6"

# Vision limits: (max long edge px, max visual tokens). tokens = ceil(w/28) * ceil(h/28)
IMAGE_TIERS = {"standard": (1568, 1568), "high": (2576, 4784)}
CHARS_PER_TOKEN = 3.6
MIN_CACHEABLE_TOKENS = 1024

DEFAULT_SETTINGS = {
    "model": DEFAULT_MODEL,
    "warn_pct_of_balance": 25.0,      # warn when one run would use this share of the remaining balance
    "run_cost_cap_usd": 2.50,         # hard stop during a run
    "run_token_cap": 3_000_000,       # warn before / stop during a run (input + output + cache tokens)
    "low_confidence": 0.75,           # papers below this model confidence are flagged
    "est_output_tokens_quiz": 420,
    "est_output_tokens_attendance": 320,
    "price_overrides": {},
}


# ----------------------------------------------------------------------------- prices
def model_info(model: str, settings: dict | None = None) -> dict:
    info = dict(MODELS.get(model) or MODELS[DEFAULT_MODEL])
    ov = ((settings or {}).get("price_overrides") or {}).get(model) or {}
    info.update({k: float(v) for k, v in ov.items() if k in ("input", "output", "cache_write", "cache_read")})
    return info


def cost(model: str, input_tokens=0, output_tokens=0, cache_write=0, cache_read=0, settings=None) -> float:
    p = model_info(model, settings)
    return (input_tokens * p["input"] + output_tokens * p["output"] + cache_write * p["cache_write"]
            + cache_read * p["cache_read"]) / 1e6


# ----------------------------------------------------------------------------- token estimation
def image_tokens(width: int, height: int, tier: str = "standard") -> int:
    max_edge, max_tok = IMAGE_TIERS.get(tier, IMAGE_TIERS["standard"])
    w, h = float(width), float(height)
    if w <= 0 or h <= 0:
        return 0
    s = min(1.0, max_edge / max(w, h))
    w, h = w * s, h * s
    tok = math.ceil(w / 28) * math.ceil(h / 28)
    while tok > max_tok:
        f = math.sqrt(max_tok / tok) * 0.995
        w, h = w * f, h * f
        tok = math.ceil(w / 28) * math.ceil(h / 28)
    return tok


def text_tokens(text_or_len) -> int:
    n = text_or_len if isinstance(text_or_len, int) else len(text_or_len or "")
    return int(math.ceil(n / CHARS_PER_TOKEN))


@dataclass
class FileInfo:
    name: str
    kind: str                 # pdf / image / text
    size_bytes: int
    pages: int = 1
    page_dims: list = field(default_factory=list)      # [(w, h)] of the main image (or rendered page)
    text_chars: int = 0
    tokens_per_page: list = field(default_factory=list)
    note: str = ""

    @property
    def tokens(self) -> int:
        return sum(self.tokens_per_page)


def inspect_file(name: str, data: bytes, tier: str = "standard") -> FileInfo:
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        info = FileInfo(name, "pdf", len(data))
        try:
            reader = PdfReader(io.BytesIO(data))
        except Exception as e:
            info.note = f"Unreadable PDF: {e}"
            return info
        info.pages = len(reader.pages)
        for pg in reader.pages:
            dims = None
            try:
                imgs = list(pg.images)
                if imgs:
                    big = max(imgs, key=lambda im: (im.image.size[0] * im.image.size[1]) if im.image else 0)
                    dims = big.image.size if big.image else None
            except Exception:
                dims = None
            if not dims:   # vector/text page: assume it is rendered at about 150 dpi
                dims = (int(float(pg.mediabox.width) / 72 * 150), int(float(pg.mediabox.height) / 72 * 150))
            try:
                chars = len(pg.extract_text() or "")
            except Exception:
                chars = 0
            info.page_dims.append(dims)
            info.text_chars += chars
            info.tokens_per_page.append(image_tokens(*dims, tier=tier) + text_tokens(chars) + 20)
        return info
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff"):
        info = FileInfo(name, "image", len(data))
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                dims = im.size
        except Exception:
            dims = (1500, 2000)
            info.note = "Image size could not be read; assumed 1500x2000."
        info.page_dims = [dims]
        info.tokens_per_page = [image_tokens(*dims, tier=tier)]
        return info
    text = data.decode("utf-8", errors="ignore")
    return FileInfo(name, "text", len(data), 1, [], len(text), [text_tokens(text)])


@dataclass
class Estimate:
    model: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    cost_without_cache_usd: float = 0.0
    lines: list = field(default_factory=list)   # [{"Step", "Calls", "Input tokens", ...}]

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + self.cache_read_tokens

    @property
    def cache_savings_usd(self) -> float:
        return max(0.0, self.cost_without_cache_usd - self.cost_usd)


def estimate_run(model: str, attendance_files: list[FileInfo], quiz_files: list[FileInfo],
                 rubric_chars: int, roster_size: int, question_file: FileInfo | None = None,
                 settings: dict | None = None, quiz_pages_per_paper: int = 1) -> Estimate:
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    est = Estimate(model)

    def add(step, calls, inp, out, cw=0, cr=0):
        c = cost(model, inp, out, cw, cr, s)
        nc = cost(model, inp + cw + cr, out, 0, 0, s)
        est.calls += calls
        est.input_tokens += inp
        est.output_tokens += out
        est.cache_write_tokens += cw
        est.cache_read_tokens += cr
        est.cost_usd += c
        est.cost_without_cache_usd += nc
        est.lines.append({"Step": step, "API calls": calls, "Input tokens": inp + cw + cr,
                          "Output tokens": out, "Cached tokens": cr, "Estimated cost (USD)": round(c, 4)})

    att_pages = [t for f in attendance_files for t in f.tokens_per_page]
    if att_pages:
        prompt = 180
        add("Attendance sheets", len(att_pages), sum(att_pages) + prompt * len(att_pages),
            s["est_output_tokens_attendance"] * len(att_pages))

    if not rubric_chars and question_file is not None:
        add("Rubric from question sheet", 1, question_file.tokens + 150, 1500)
        rubric_chars = 1500 * 4

    quiz_pages = [t for f in quiz_files for t in f.tokens_per_page]
    if quiz_pages:
        per = max(1, int(quiz_pages_per_paper))
        papers = [sum(quiz_pages[i:i + per]) for i in range(0, len(quiz_pages), per)]
        system = text_tokens(rubric_chars) + 1100 + roster_size * 4
        n = len(papers)
        if system >= MIN_CACHEABLE_TOKENS and n > 1:
            add("Quiz papers", n, sum(papers) + 30 * n, s["est_output_tokens_quiz"] * n,
                cw=system, cr=system * (n - 1))
        else:
            add("Quiz papers", n, sum(papers) + 30 * n + system * n, s["est_output_tokens_quiz"] * n)
    return est


# ----------------------------------------------------------------------------- ledger
def load_ledger() -> dict:
    led = read_json(LEDGER_FILE, None) or {}
    led.setdefault("balance_usd", 0.0)
    led.setdefault("balance_set_at", None)
    led.setdefault("total_spent_usd", 0.0)
    led.setdefault("history", [])
    st = led.setdefault("settings", {})
    for k, v in DEFAULT_SETTINGS.items():
        st.setdefault(k, v)
    return led


def save_ledger(led: dict) -> str:
    return write_json(LEDGER_FILE, led, backup=True, course="GLOBAL")


def set_balance(led: dict, amount: float, note: str = "") -> dict:
    led["history"].append({"at": now_iso(), "type": "set_balance", "amount_usd": round(float(amount), 4),
                           "previous_usd": round(led["balance_usd"], 4), "note": note})
    led["balance_usd"] = float(amount)
    led["balance_set_at"] = now_iso()
    return led


def top_up(led: dict, amount: float, note: str = "") -> dict:
    led["balance_usd"] = float(led["balance_usd"]) + float(amount)
    led["history"].append({"at": now_iso(), "type": "top_up", "amount_usd": round(float(amount), 4), "note": note})
    return led


def record_run(led: dict, usage: "UsageTracker", label: str) -> dict:
    led["balance_usd"] = float(led["balance_usd"]) - usage.cost
    led["total_spent_usd"] = float(led["total_spent_usd"]) + usage.cost
    led["history"].append({"at": now_iso(), "type": "grading_run", "amount_usd": round(-usage.cost, 4),
                           "note": label, "model": usage.model, "calls": usage.calls,
                           "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                           "cache_read_tokens": usage.cache_read, "cache_write_tokens": usage.cache_write})
    return led


# ----------------------------------------------------------------------------- thresholds
def threshold_warnings(est: Estimate, led: dict) -> list[tuple[str, str]]:
    """[(level, message)] with level in {"error", "warning", "info"}."""
    s = led["settings"]
    bal = float(led["balance_usd"])
    out = []
    if bal > 0 and est.cost_usd > bal:
        out.append(("error", f"The estimated cost (${est.cost_usd:.2f}) is higher than the remaining balance "
                             f"(${bal:.2f}). Top up the account before running."))
    elif bal > 0 and est.cost_usd >= bal * s["warn_pct_of_balance"] / 100.0:
        out.append(("warning", f"This run would use {100 * est.cost_usd / bal:.0f}% of the remaining balance "
                               f"(${est.cost_usd:.2f} of ${bal:.2f}); your warning threshold is "
                               f"{s['warn_pct_of_balance']:g}%."))
    if bal <= 0:
        out.append(("info", "No account balance is recorded yet. Enter it under Billing & Settings to get "
                            "balance warnings."))
    cap = float(s["run_cost_cap_usd"])
    if cap > 0:
        if est.cost_usd > cap:
            out.append(("error", f"The estimated cost (${est.cost_usd:.2f}) exceeds the per-run cost cap "
                                 f"(${cap:.2f}). The run will stop when the cap is reached; raise the cap or "
                                 f"grade fewer papers."))
        elif est.cost_usd >= 0.8 * cap:
            out.append(("warning", f"The estimated cost (${est.cost_usd:.2f}) is close to the per-run cost cap "
                                   f"(${cap:.2f})."))
    tcap = int(s["run_token_cap"])
    if tcap > 0:
        if est.total_tokens > tcap:
            out.append(("error", f"Estimated tokens ({est.total_tokens:,}) exceed the per-run token cap "
                                 f"({tcap:,})."))
        elif est.total_tokens >= 0.8 * tcap:
            out.append(("warning", f"Estimated tokens ({est.total_tokens:,}) are close to the per-run token cap "
                                   f"({tcap:,})."))
    return out


# ----------------------------------------------------------------------------- live tracking
class BudgetExceeded(RuntimeError):
    pass


@dataclass
class UsageTracker:
    model: str
    settings: dict = field(default_factory=dict)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_write

    def add(self, response) -> float:
        u = getattr(response, "usage", None)
        if u is None:
            return 0.0
        inp = getattr(u, "input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        c = cost(self.model, inp, out, cw, cr, self.settings)
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read += cr
        self.cache_write += cw
        self.cost += c
        return c

    def check_caps(self) -> None:
        cap = float(self.settings.get("run_cost_cap_usd") or 0)
        if cap > 0 and self.cost >= cap:
            raise BudgetExceeded(f"Per-run cost cap reached: ${self.cost:.2f} >= ${cap:.2f}. Grading stopped; "
                                 f"the papers graded so far are kept in the review queue.")
        tcap = int(self.settings.get("run_token_cap") or 0)
        if tcap > 0 and self.total_tokens >= tcap:
            raise BudgetExceeded(f"Per-run token cap reached: {self.total_tokens:,} >= {tcap:,}. Grading stopped; "
                                 f"the papers graded so far are kept in the review queue.")

    def as_dict(self) -> dict:
        return {"model": self.model, "calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cache_read_tokens": self.cache_read,
                "cache_write_tokens": self.cache_write, "cost_usd": round(self.cost, 5)}
