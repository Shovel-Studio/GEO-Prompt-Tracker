"""Camoufox persistent-context launcher.

One browser profile holds the logins for all three platforms. `launch_browser`
yields a Playwright BrowserContext whose cookies/session persist to disk, so you
log in once (see cli.login) and every later run reuses those sessions.
"""
from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from camoufox.sync_api import Camoufox
from playwright.sync_api import BrowserContext

from .. import config

# When a platform forces a visible window (ChatGPT), keep it small and tucked in
# the bottom-right corner so it doesn't cover the user's work.
_WINDOW_W, _WINDOW_H = 760, 640


def _corner_position() -> tuple[int, int]:
    """Bottom-right corner of the main display, with a safe fallback size."""
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "Finder" to get bounds of window of desktop'],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()  # e.g. "0, 0, 1728, 1117"
        _, _, screen_w, screen_h = (int(p.strip()) for p in out.split(","))
    except Exception:
        screen_w, screen_h = 1440, 900
    return max(0, screen_w - _WINDOW_W - 8), max(0, screen_h - _WINDOW_H - 60)


def _pin_window_to_corner() -> None:
    """Seed Firefox's saved window geometry (xulstore.json in the profile) so a
    visible browser opens as a small corner window instead of front-and-center."""
    x, y = _corner_position()
    store = Path(config.PROFILE_DIR) / "xulstore.json"
    try:
        data = json.loads(store.read_text()) if store.exists() else {}
    except Exception:
        data = {}
    data.setdefault("chrome://browser/content/browser.xhtml", {})["main-window"] = {
        "screenX": str(x), "screenY": str(y),
        "width": str(_WINDOW_W), "height": str(_WINDOW_H),
        "sizemode": "normal",
    }
    try:
        store.write_text(json.dumps(data))
    except Exception:
        pass


def _clear_stale_locks() -> None:
    """Remove Firefox profile locks left behind by a browser that died hard.

    Safe because the app only ever runs one browser against this profile at a
    time; a leftover lock would otherwise deadlock the next launch.
    """
    for name in (".parentlock", "lock", ".lock"):
        try:
            (Path(config.PROFILE_DIR) / name).unlink()
        except (FileNotFoundError, OSError):
            pass


@contextmanager
def launch_browser(headless: bool = True) -> Iterator[BrowserContext]:
    Path(config.PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    _clear_stale_locks()
    extra = {}
    if not headless:
        _pin_window_to_corner()
        extra["window"] = (_WINDOW_W, _WINDOW_H)
    # persistent_context=True makes Camoufox yield a BrowserContext bound to
    # user_data_dir, so sessions survive across runs. humanize adds cursor/typing
    # jitter that helps against bot detection.
    with Camoufox(
        headless=headless,
        persistent_context=True,
        user_data_dir=str(config.PROFILE_DIR),
        humanize=True,
        os=("macos",),
        **extra,
    ) as context:
        context.set_default_timeout(30_000)
        yield context
