"""Batch orchestrator: prompts x platforms x N runs, gently paced, resumable.

Results are saved after every single run, so a crash or Ctrl-C loses at most one
answer and re-running skips everything already filled.

Resilience: the browser/driver occasionally dies mid-batch (a Firefox page-error
can crash the Playwright driver). Rather than let every later run time out against
a dead context, we detect that and relaunch a fresh browser, so the batch keeps
going instead of hanging.
"""
from __future__ import annotations

import random
import time
import traceback

from .analyze import analyze
from .engines import ENGINES
from .engines.browser import launch_browser
from .excel_io import ANALYSIS_FAILED, Tracker
from .sources import normalize_sources
from . import config

# Relaunch the browser after this many consecutive failures (a healthy run resets
# the counter), or immediately when an error looks like a dead browser/context.
_RELAUNCH_AFTER = 3
# If restarts stop helping, the platform is almost certainly rate-limiting us.
# Close the browser and cool off after this many restarts with no success in
# between, and give up on the platform entirely after this many total.
_COOLDOWN_AFTER_RESTARTS = 2
_GIVE_UP_AFTER_RESTARTS = 4
_COOLDOWN_S = 600
_DEAD_BROWSER_HINTS = (
    "target page, context or browser has been closed",
    "browser has been closed", "connection closed", "target closed",
    "crash", "ns_error", "websocket", "pipe", "browsercontext",
)


def _sleep(bounds: tuple[int, int]) -> None:
    time.sleep(random.uniform(*bounds))


def _looks_dead(err: Exception) -> bool:
    msg = str(err).lower()
    return any(h in msg for h in _DEAD_BROWSER_HINTS)


def _friendly_error(err: Exception) -> str:
    """Plain-English version of a technical error, for the live log."""
    m = str(err).lower()
    if "timeout" in m:
        return "the page didn't respond in time"
    if any(h in m for h in ("closed", "crash", "target", "websocket", "pipe")):
        return "the browser dropped the connection"
    if "empty response" in m:
        return "the answer came back empty"
    if "invalid response" in m:
        return "the answer looked incomplete"
    if any(h in m for h in ("goto", "navigat", "net", "ns_error")):
        return "couldn't load the page"
    return "an unexpected problem occurred"


def _save_failure_screenshot(browser: "_Browser", platform: str, pnum: int, run: int) -> str:
    """Capture what the page showed when a run failed, for diagnosis. Saved in
    the local data dir (never the repo). Returns the path, or "" if impossible."""
    if browser._ctx is None or not browser._ctx.pages:
        return ""
    try:
        folder = config.DATA_DIR / "failures"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{platform}_p{pnum}_r{run}.png"
        browser._ctx.pages[0].screenshot(path=str(path), timeout=10_000)
        return str(path)
    except Exception:
        return ""


def _describe(recs: list[dict]) -> str:
    if not recs:
        return "no recommendations found"
    return f"top pick: {recs[0]['name']} · {len(recs)} recommended"


class _Browser:
    """Owns a Camoufox context and can relaunch it on demand."""

    def __init__(self, headless: bool):
        self.headless = headless
        self._cm = None
        self._ctx = None

    def page(self):
        if self._ctx is None:
            self._cm = launch_browser(headless=self.headless)
            self._ctx = self._cm.__enter__()
        return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def restart(self) -> None:
        self.close()
        self.page()  # eagerly reopen so the next run has a live context

    def close(self) -> None:
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
        self._cm = None
        self._ctx = None


# ChatGPT's Cloudflare serves a blank page to headless browsers, so it must run
# visible. Perplexity and Gemini are fine headless.
_MUST_BE_VISIBLE = {"ChatGPT"}


def _run_platform(tracker: Tracker, platform: str, prompts, headless: bool, runs: int) -> None:
    engine = ENGINES[platform]
    effective_headless = headless and platform not in _MUST_BE_VISIBLE
    if headless and not effective_headless:
        print(f"   ({platform} needs a visible window — headless is blocked by its bot "
              f"check — so it opens as a small window in the bottom-right corner)")
    browser = _Browser(effective_headless)
    consecutive_failures = 0
    restarts_since_success = 0
    try:
        for pnum, ptext in prompts:
            for run in range(1, runs + 1):
                if tracker.is_filled(pnum, platform, run):
                    continue
                where = f"{platform} · prompt {pnum}, run {run} of {runs}"
                try:
                    page = browser.page()
                    engine.new_chat(page)
                    result = engine.ask(page, ptext)
                    if not result.response_text.strip():
                        # Don't record a blank row as done — raise so it retries.
                        raise RuntimeError("empty response")
                    sources = normalize_sources(result.sources)
                    result.sources = sources
                    # An analysis failure (bad key, API down) must not look like a
                    # browser failure: keep the answer, flag it for `reanalyze`.
                    try:
                        recs, analysis_err = analyze(ptext, result), ""
                    except Exception as err:
                        recs, analysis_err = [], f"{ANALYSIS_FAILED}: {err}"
                        print(f"   (couldn't extract recommendations — answer saved; "
                              f"run `python -m geo.cli reanalyze` later)")
                    tracker.write_result(pnum, platform, run, sources, result.response_text, recs)
                    if analysis_err:
                        tracker.note_error(pnum, platform, run, analysis_err)
                    tracker.rebuild_summary_sheets()
                    print(f"✓  {where} — {_describe(recs)} · "
                          f"{len(sources)} sources cited")
                    consecutive_failures = 0
                    restarts_since_success = 0
                except Exception as err:  # never let one run kill the batch
                    tracker.note_error(pnum, platform, run, f"ERROR: {err}")
                    print(f"⚠  {where} — {_friendly_error(err)}; will retry on resume")
                    shot = _save_failure_screenshot(browser, platform, pnum, run)
                    detail = str(err).strip().splitlines()[0][:200] if str(err).strip() else type(err).__name__
                    print(f"   details: {detail}" + (f"\n   screenshot: {shot}" if shot else ""))
                    consecutive_failures += 1
                    if _looks_dead(err) or consecutive_failures >= _RELAUNCH_AFTER:
                        tracker.save()
                        restarts_since_success += 1
                        if restarts_since_success >= _GIVE_UP_AFTER_RESTARTS:
                            print(f"⏹  {platform} still isn't answering after several "
                                  f"restarts and a long pause — it's very likely "
                                  f"rate-limiting us. Stopping {platform} for now; "
                                  f"re-run the same command later and it will resume "
                                  f"exactly where it left off.")
                            return
                        if restarts_since_success >= _COOLDOWN_AFTER_RESTARTS:
                            print(f"⏸  Restarting hasn't helped — closing the "
                                  f"{platform} browser and cooling off for "
                                  f"{_COOLDOWN_S // 60} minutes (this usually means "
                                  f"rate limiting).")
                            browser.close()
                            # Sleep in slices with a heartbeat so the webapp's
                            # stall watchdog doesn't mistake the pause for a hang.
                            for remaining in range(_COOLDOWN_S, 0, -60):
                                print(f"   …retrying {platform} in {max(1, remaining // 60)} minute(s)")
                                time.sleep(min(60, remaining))
                        print(f"↻  Restarting the {platform} browser after a couple of hiccups…")
                        try:
                            browser.restart()
                        except Exception:
                            print(f"   Couldn't restart the {platform} browser; will keep trying.")
                        consecutive_failures = 0
                tracker.save()
                _sleep(config.DELAY_BETWEEN_RUNS)
            _sleep(config.DELAY_BETWEEN_PROMPTS)
    finally:
        browser.close()


def run_batch(tracker: Tracker, platforms: list[str], headless: bool,
              runs: int, prompt_limit: int | None = None) -> None:
    prompts = tracker.prompts()
    if prompt_limit:
        prompts = prompts[:prompt_limit]

    for platform in platforms:
        remaining = sum(
            1 for pnum, _ in prompts for run in range(1, runs + 1)
            if not tracker.is_filled(pnum, platform, run)
        )
        print(f"\nStarting {platform} — {len(prompts)} prompts × {runs} runs each "
              f"({remaining} still to do).")
        _run_platform(tracker, platform, prompts, headless, runs)

    tracker.rebuild_summary_sheets()
    tracker.save()
    print("\n✓  All done — every result is saved and the leaderboard is rebuilt.")
