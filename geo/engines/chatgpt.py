"""ChatGPT (chatgpt.com) engine.

Selectors reflect the current logged-in UI and are the most likely thing to need
tuning on the first real run — they are all kept here at the top for that reason.
"""
from __future__ import annotations

from playwright.sync_api import Page

from .. import config
from .base import CaptureResult, Source
from .common import wait_for_stable_text, collect_anchor_sources

URL = "https://chatgpt.com/?temporary-chat=true"
EDITOR = "#prompt-textarea, div[contenteditable='true']"
SEND_BUTTON = "button[data-testid='send-button'], button[aria-label*='Send']"
ASSISTANT_TURN = "div[data-message-author-role='assistant']"
STOP_BUTTON = "button[data-testid='stop-button'], button[aria-label*='Stop']"


class ChatGPTEngine:
    name = "ChatGPT"
    url = URL

    def new_chat(self, page: Page) -> None:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(EDITOR, timeout=30_000)

    def ask(self, page: Page, prompt: str) -> CaptureResult:
        editor = page.locator(EDITOR).first
        editor.click()
        editor.fill("")
        editor.type(prompt, delay=15)
        page.wait_for_timeout(400)
        # Enter submits in ChatGPT; the send button is often covered by a tooltip
        # that intercepts real clicks, so Enter is the reliable primary path.
        editor.press("Enter")
        try:
            page.wait_for_selector(ASSISTANT_TURN, timeout=12_000)
        except Exception:
            btn = page.locator(SEND_BUTTON).first
            if btn.count():
                btn.click(force=True)  # force past any overlay/tooltip
            page.wait_for_selector(ASSISTANT_TURN, timeout=30_000)
        self._wait_generation_done(page)
        turn = page.locator(ASSISTANT_TURN).last
        text = (turn.inner_text() or "").strip()
        if not text:
            text = wait_for_stable_text(page, page.locator(ASSISTANT_TURN),
                                        timeout_s=config.RESPONSE_TIMEOUT_S)
        return CaptureResult(response_text=text, sources=self._sources(page, turn))

    def _wait_generation_done(self, page: Page) -> None:
        # The stop button is present only while streaming; wait for it to vanish.
        try:
            page.wait_for_selector(STOP_BUTTON, timeout=8_000)
        except Exception:
            pass
        try:
            page.wait_for_selector(STOP_BUTTON, state="detached",
                                   timeout=config.RESPONSE_TIMEOUT_S * 1000)
        except Exception:
            pass
        page.wait_for_timeout(1_000)

    def _sources(self, page, turn) -> list[Source]:
        sources: list[Source] = []
        # Try to open the "Sources" panel if the answer used web search.
        try:
            btn = page.get_by_role("button", name="Sources")
            if btn.count():
                btn.first.click(force=True)
                page.wait_for_timeout(1_200)
                panel = page.locator("ul li a[target='_blank'][href^='http']")
                for i in range(panel.count()):
                    a = panel.nth(i)
                    sources.append(Source(
                        url=a.get_attribute("href") or "",
                        title=(a.inner_text() or "").strip()[:300],
                    ))
        except Exception:
            pass
        # Always supplement with inline links from the answer itself.
        sources.extend(collect_anchor_sources(turn))
        return sources


engine = ChatGPTEngine()
