"""Perplexity (perplexity.ai) engine. Perplexity is the most citation-rich of the
three — it renders an explicit Sources list for every answer."""
from __future__ import annotations

from playwright.sync_api import Page

from .. import config
from .base import CaptureResult, Source
from .common import wait_for_stable_text, collect_anchor_sources

URL = "https://www.perplexity.ai/"
EDITOR = "textarea[placeholder], div[contenteditable='true']"
ANSWER = "div[dir='auto'], .prose"


class PerplexityEngine:
    name = "Perplexity"
    url = URL

    def new_chat(self, page: Page) -> None:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(EDITOR, timeout=30_000)

    def ask(self, page: Page, prompt: str) -> CaptureResult:
        editor = page.locator(EDITOR).first
        editor.click()
        editor.fill(prompt)
        page.wait_for_timeout(400)
        editor.press("Enter")

        # Perplexity streams into the answer prose; wait until it settles.
        text = wait_for_stable_text(page, page.locator(ANSWER),
                                    timeout_s=config.RESPONSE_TIMEOUT_S)
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
