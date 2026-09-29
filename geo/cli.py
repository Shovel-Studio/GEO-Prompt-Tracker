"""Command line entry point.

  python -m geo.cli check                 # validate config before running
  python -m geo.cli login                 # one-time: log into each platform
  python -m geo.cli run                    # full batch (headless)
  python -m geo.cli run --headful --limit 1 --runs 1   # smoke test one prompt
  python -m geo.cli reanalyze             # extract recommendations that failed earlier
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .engines import ENGINES
from .engines.browser import launch_browser
from .excel_io import Tracker, ensure_working_copy
from .runner import run_batch


def cmd_check(_args) -> int:
    ok = True
    prompts = config.load_prompts()
    if not prompts:
        print(f"Prompts       : NONE found in {config.PROMPTS_FILE}")
        ok = False
    else:
        total = len(prompts) * len(config.PLATFORMS) * config.RUNS_PER_PROMPT
        print(f"Prompts       : {len(prompts)} prompts -> {total} runs "
              f"({len(config.PLATFORMS)} platforms x {config.RUNS_PER_PROMPT} runs)")
        bracketed = [p for p in prompts if "[" in p and "]" in p]
        if bracketed:
            print(f"  note: {len(bracketed)} prompt(s) still contain [brackets] and will run literally:")
            for p in bracketed:
                print(f"        - {p}")

    if config.analysis_key():
        print(f"Analysis key  : set ({config.ANALYSIS_PROVIDER}) — recommendations will be extracted")
    else:
        print(f"Analysis key  : none — answers + sources still save; "
              f"recommendations and leaderboard stay empty")

    wb = ensure_working_copy()
    print(f"Output file   : {wb}")
    print("OK" if ok else "FIX THE ITEMS ABOVE")
    return 0 if ok else 1


def cmd_login(_args) -> int:
    print("Opening one tab per platform. Log into each (in any order), then come\n"
          "back here and press Enter once. Sessions are saved for future runs.\n")
    with launch_browser(headless=False) as ctx:
        # Reuse the initial blank page for the first provider, add tabs for the rest.
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        names = list(ENGINES)
        for i, name in enumerate(names):
            page = first if i == 0 else ctx.new_page()
            try:
                page.goto(ENGINES[name].url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as err:
                print(f"  (warning) {name} tab didn't finish loading: {err}")
            print(f"  tab {i + 1}: {name} -> {ENGINES[name].url}")
        try:
            first.bring_to_front()
        except Exception:
            pass
        input("\nLog into all three tabs, then press Enter to save sessions... ")
        print("Sessions saved to", config.PROFILE_DIR)
    return 0


def cmd_run(args) -> int:
    prompts = config.load_prompts()
    if not prompts:
        print(f"No prompts in {config.PROMPTS_FILE}"); return 1
    wb = ensure_working_copy(prompts)
    print(f"Saving results to: {wb.name}")
    tracker = Tracker(wb)
    tracker.sync_structure(prompts)  # build/refresh Log to match prompts.txt
    tracker.save()
    platforms = args.platforms or config.PLATFORMS
    run_batch(
        tracker,
        platforms=platforms,
        headless=not args.headful,
        runs=args.runs or config.RUNS_PER_PROMPT,
        prompt_limit=args.limit,
    )
    return 0


def cmd_reanalyze(_args) -> int:
    """Extract recommendations for saved answers that missed analysis."""
    from .analyze import analyze
    from .engines.base import CaptureResult
    if not config.analysis_key():
        print("No analysis key set in .env"); return 1
    wb = ensure_working_copy()
    tracker = Tracker(wb)
    pending = tracker.needs_analysis()
    print(f"{len(pending)} answer(s) in {wb.name} need recommendations extracted.")
    for i, (row, prompt, answer) in enumerate(pending, start=1):
        recs = analyze(prompt, CaptureResult(response_text=answer))
        tracker.set_recommendations(row, recs)
        top = recs[0]["name"] if recs else "none found"
        print(f"✓  {i} of {len(pending)} — top pick: {top}")
    tracker.rebuild_summary_sheets()
    tracker.save()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="geo")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("login").set_defaults(func=cmd_login)
    sub.add_parser("reanalyze").set_defaults(func=cmd_reanalyze)
    r = sub.add_parser("run")
    r.add_argument("--headful", action="store_true", help="show the browser")
    r.add_argument("--limit", type=int, help="only the first N prompts (smoke test)")
    r.add_argument("--runs", type=int, help="override runs per prompt")
    r.add_argument("--platforms", nargs="+", choices=list(ENGINES), help="subset of platforms")
    r.set_defaults(func=cmd_run)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
