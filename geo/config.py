"""Central configuration — run behavior, paths, and the analysis model."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True: runs are spawned by the web UI and inherit its environment, so
# without this a key changed in .env after the UI started would be ignored.
load_dotenv(ROOT / ".env", override=True)

# ── Run behavior ─────────────────────────────────────────────────────────────
RUNS_PER_PROMPT = 5
PLATFORMS = ["ChatGPT", "Perplexity", "Gemini"]  # match the template's Platform column

# Human-like pacing (seconds) to stay under bot/rate-limit radar.
DELAY_BETWEEN_RUNS = (6, 14)      # min, max — same prompt, next run
DELAY_BETWEEN_PROMPTS = (10, 25)  # min, max — next prompt
RESPONSE_TIMEOUT_S = 240          # max wait for one answer to finish generating

# ── Paths ────────────────────────────────────────────────────────────────────
PROMPTS_FILE = ROOT / "prompts.txt"       # source of truth for prompts
# Results stay on this machine: kept outside the project because ~/Desktop may
# be synced to iCloud. Override with GEO_DATA_DIR in .env.
DATA_DIR = Path(os.getenv("GEO_DATA_DIR") or Path.home() / "Library" / "Application Support" / "GEO Tool")
OUTPUT_DIR = DATA_DIR / "output"
OUTPUT_STEM = "geo_prompt_tracker"  # output/<stem>_<timestamp>.xlsx
PROFILE_DIR = ROOT / ".browser-profile"  # persistent login sessions live here

# ── Analysis LLM ─────────────────────────────────────────────────────────────
ANALYSIS_PROVIDER = os.getenv("ANALYSIS_PROVIDER", "openai").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def resolve_output_path(new: bool = False) -> Path:
    """Resume the most recent timestamped output if one exists (unless `new`),
    else mint one stamped with the current date/time (self-labelling)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(OUTPUT_DIR.glob(f"{OUTPUT_STEM}_*.xlsx"), key=lambda p: p.stat().st_mtime)
    if existing and not new:
        return existing[-1]
    from datetime import datetime
    path = OUTPUT_DIR / f"{OUTPUT_STEM}_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
    if path.exists():  # two new files within the same minute
        path = path.with_name(f"{path.stem}{datetime.now():%S}.xlsx")
    return path


def analysis_key() -> str:
    """The active analysis key, or "" if none is set (auto-fill then disabled)."""
    return ANTHROPIC_API_KEY if ANALYSIS_PROVIDER == "anthropic" else OPENAI_API_KEY


def load_prompts() -> list[str]:
    """Prompts from prompts.txt, ignoring blanks and # comments. On a fresh
    clone prompts.txt (git-ignored) is seeded from prompts.example.txt."""
    example = ROOT / "prompts.example.txt"
    if not PROMPTS_FILE.exists() and example.exists():
        PROMPTS_FILE.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    if not PROMPTS_FILE.exists():
        return []
    lines = PROMPTS_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
