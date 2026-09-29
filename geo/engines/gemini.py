"""Gemini (gemini.google.com) engine.

Gemini's citation links are often Google/Vertex redirect URLs; sources.clean_url
unwraps those to the real domain downstream.
"""
from __future__ import annotations

from playwright.sync_api import Page

from .. import config
from .base import CaptureResult, Source
from .common import wait_for_stable_text, collect_anchor_sources

URL = "https://gemini.google.com/app"
EDITOR = "div.ql-editor[contenteditable='true'], rich-textarea div[contenteditable='true']"
SEND_BUTTON = "button[aria-label*='Send'], button.send-button"
ANSWER = "message-content, model-response .markdown"


class GeminiEngine:
    name = "Gemini"
    url = URL

    def new_chat(self, page: Page) -> None:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(EDITOR, timeout=30_000)

    def ask(self, page: Page, prompt: str) -> CaptureResult:
        editor = page.locator(EDITOR).first
        editor.click()
        editor.type(prompt, delay=15)
        page.wait_for_timeout(400)
        btn = page.locator(SEND_BUTTON).first
        if btn.count() and btn.is_enabled():
            btn.click()
        else:
            editor.press("Enter")

        text = wait_for_stable_text(page, page.locator(ANSWER),
                                    timeout_s=config.RESPONSE_TIMEOUT_S)
        return CaptureResult(response_text=text, sources=self._sources(page))

    # Gemini's own app/nav hosts — never real citations.
    _APP_HOSTS = ("support.google.com", "accounts.google.com", "policies.google.com",
                  "gemini.google.com", "myactivity.google.com", "google.com/intl")
    _CITATION_BTN = "button[aria-label*='View source details']"
    _CARD_ANCHOR = ("multi-source-hovered-card a[href^='http'], "
                    "inline-source-card a[href^='http']")

    _MAX_CITATIONS = 25  # bound the scrape so one answer can't stall the batch

    def _sources(self, page: Page) -> list[Source]:
        # Gemini renders citations as buttons; the real URL only appears in a
        # source card once that citation is opened. Open each in turn and scrape.
        # This UI is fragile, so capture is best-effort — the response text and
        # mention detection do not depend on it.
        sources: list[Source] = []
        buttons = page.locator(self._CITATION_BTN)
        count = min(buttons.count(), self._MAX_CITATIONS)
        for i in range(count):
            try:
                buttons.nth(i).click(force=True, timeout=4_000)
                page.wait_for_timeout(300)
                cards = page.locator(self._CARD_ANCHOR)
                for j in range(cards.count()):
                    a = cards.nth(j)
                    href = a.get_attribute("href") or ""
                    if href and not any(h in href for h in self._APP_HOSTS):
                        sources.append(Source(url=href, title=(a.inner_text() or "").strip()[:300]))
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
            except Exception:
                continue
        # Supplement with any inline links directly in the answer.
        for s in collect_anchor_sources(page.locator(ANSWER).last):
            if not any(h in s.url for h in self._APP_HOSTS):
                sources.append(s)
        return sources


engine = GeminiEngine()
