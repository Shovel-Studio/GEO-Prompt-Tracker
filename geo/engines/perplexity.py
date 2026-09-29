"""Perplexity (perplexity.ai) engine. Perplexity is the most citation-rich of the
three — it renders an explicit Sources list for every answer."""
from __future__ import annotations

import re

from playwright.sync_api import Page

from .. import config
from .base import CaptureResult, Source
from .common import wait_for_stable_text, collect_anchor_sources

URL = "https://www.perplexity.ai/"
EDITOR = "textarea[placeholder], div[contenteditable='true']"
ANSWER = "div[dir='auto'], .prose"


# Shown (as text, and sometimes as a pop-up over the page) only when logged out.
LOGGED_OUT_TEXT = re.compile(r"log ?in or sign up", re.I)
MODAL_CLOSE = "button[aria-label='Close']"


class PerplexityEngine:
    name = "Perplexity"
    url = URL
    _warned_logged_out = False

    def new_chat(self, page: Page) -> None:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(EDITOR, timeout=30_000)
        page.wait_for_timeout(1_500)  # the sign-up pop-up appears shortly after load
        self._handle_logged_out(page)

    def _handle_logged_out(self, page: Page) -> None:
        """Close the logged-out sign-up pop-up (it covers the prompt box and makes
        the answer come back empty) and warn once that the session has expired."""
        if not page.get_by_text(LOGGED_OUT_TEXT).count():
            return
        if not self._warned_logged_out:
            PerplexityEngine._warned_logged_out = True
            print("   ⚠  Perplexity is LOGGED OUT — answers will be shorter than a "
                  "logged-in session. Log in again via Step 1 (Open login browser).")
        try:
            close = page.locator(MODAL_CLOSE)
            for i in range(close.count()):
                if close.nth(i).is_visible():
                    close.nth(i).click(timeout=3_000)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        except Exception:
            pass

    def ask(self, page: Page, prompt: str) -> CaptureResult:
        self._handle_logged_out(page)  # the pop-up can appear late
        editor = page.locator(EDITOR).first
        editor.click()
        editor.fill(prompt)
        page.wait_for_timeout(400)
        editor.press("Enter")

        # Perplexity streams into the answer prose; wait until it settles.
        text = wait_for_stable_text(page, page.locator(ANSWER),
                                    timeout_s=config.RESPONSE_TIMEOUT_S,
                                    stable_for_s=4.0, min_chars=80)
        return CaptureResult(response_text=text, sources=self._sources(page))

    def _sources(self, page: Page) -> list[Source]:
        sources: list[Source] = []
        try:
            tab = page.get_by_role("button", name="Sources")
            if tab.count():
                tab.first.click()
                page.wait_for_timeout(1_200)
        except Exception:
            pass
        # Source cards are external links; grab them from the whole answer region.
        try:
            anchors = page.locator("a[href^='http']:not([href*='perplexity.ai'])")
            for i in range(anchors.count()):
                a = anchors.nth(i)
                sources.append(Source(
                    url=a.get_attribute("href") or "",
                    title=(a.get_attribute("aria-label") or a.inner_text() or "").strip()[:300],
                ))
        except Exception:
            sources.extend(collect_anchor_sources(page.locator("body")))
        return sources


engine = PerplexityEngine()
