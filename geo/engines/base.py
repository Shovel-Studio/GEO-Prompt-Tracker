"""Shared types and the provider interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from playwright.sync_api import Page


@dataclass
class Source:
    url: str
    title: str = ""
    domain: str = ""


@dataclass
class CaptureResult:
    """Raw output of running one prompt once against one platform."""
    response_text: str = ""
    sources: list[Source] = field(default_factory=list)
    error: str = ""


class Engine(Protocol):
    """One implementation per platform. Selectors live inside each engine so
    tuning a provider never touches the runner."""

    name: str
    url: str

    def new_chat(self, page: Page) -> None:
        """Reset to a fresh conversation so each run is independent."""

    def ask(self, page: Page, prompt: str) -> CaptureResult:
        """Submit the prompt, wait for completion, return text + sources."""
