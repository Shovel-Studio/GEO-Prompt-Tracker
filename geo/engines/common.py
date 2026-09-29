"""Helpers shared across engines: completion detection and link scraping."""
from __future__ import annotations

import time

from playwright.sync_api import Page, Locator

from .base import Source


def wait_for_stable_text(
    page: Page,
    locator: Locator,
    timeout_s: float,
    stable_for_s: float = 3.0,
    poll_s: float = 1.0,
    min_chars: int = 0,
) -> str:
    """Poll a container's text until it stops growing for `stable_for_s`.

    Provider-agnostic completion detector: streaming answers grow, then hold
    steady once generation finishes. Text shorter than `min_chars` never counts
    as finished, so a status line ("Researching…") that sits still during a
    slow search isn't mistaken for the answer. Returns the final text (may be
    empty or short if the timeout is hit).
    """
    deadline = time.time() + timeout_s
    last = ""
    last_change = time.time()
    while time.time() < deadline:
        try:
            current = (locator.last.inner_text(timeout=5_000) or "").strip()
        except Exception:
            current = last
        if current != last:
            last = current
            last_change = time.time()
        elif len(current) > max(min_chars, 0) and (time.time() - last_change) >= stable_for_s:
            return current
        time.sleep(poll_s)
    return last


def collect_anchor_sources(scope: Locator) -> list[Source]:
    """Fallback source scraper: every external http link inside `scope`.

    Used when a provider has no dedicated sources panel, or to supplement it.
    """
    sources: list[Source] = []
    try:
        anchors = scope.locator('a[href^="http"]')
        for i in range(anchors.count()):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            if not href:
                continue
            title = (a.get_attribute("aria-label") or a.inner_text() or "").strip()
            sources.append(Source(url=href, title=title[:300]))
    except Exception:
        pass
    return sources
