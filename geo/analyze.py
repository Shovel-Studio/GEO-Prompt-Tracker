"""Turn a captured answer into a ranked list of what the AI recommended.

A cheap LLM call reads the answer and returns every brand / product / company it
recommends, in the order the answer presents them. The runner writes that list
to the Log, and excel_io aggregates it into the Leaderboard.
"""
from __future__ import annotations

import json
import re

from . import config
from .engines.base import CaptureResult

_SYSTEM = (
    "You are a precise GEO analyst. Reply ONLY with JSON. Extract only what the "
    "answer actually recommends; never add names that are not in the answer."
)


def _prompt(question: str, text: str) -> str:
    return f"""A user asked an AI assistant: "{question}"

List every brand, product, company, or service the answer below recommends or
suggests as an answer to that question, in the order the answer ranks or
presents them (first = top pick). Skip names that are only mentioned in passing
or as something to avoid.

Return JSON: {{"recommendations": [{{"name": "...", "brand": "..."}}, ...]}}
- "name": the specific item as the answer names it, tidied (e.g. "Levi's 501 Original").
- "brand": the canonical parent brand/company (e.g. "Levi's"). If the item IS a
  brand or company, repeat it. Use one consistent spelling per brand.
- Return an empty list if the answer recommends nothing.

ANSWER:
<answer>
{text[:12000]}
</answer>"""


def _call_llm(prompt: str) -> dict:
    if config.ANALYSIS_PROVIDER == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=config.ANTHROPIC_MODEL, max_tokens=2000, temperature=0,
            system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text if msg.content else "{}"
    else:
        from openai import OpenAI
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL, temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content or "{}"
    return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    """Parse model JSON, tolerating ```json fences (Anthropic adds them)."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)  # last-ditch: grab first object
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return {}


def analyze(question: str, result: CaptureResult) -> list[dict]:
    """Return the ranked recommendations [{name, brand}, ...] for one answer.

    Needs the analysis LLM; without a key this returns [] and the answer text and
    sources are still saved for manual review."""
    if not config.analysis_key() or not result.response_text.strip():
        return []
    data = _call_llm(_prompt(question, result.response_text))
    out, seen = [], set()
    for item in data.get("recommendations") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        brand = str(item.get("brand") or name).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "brand": brand})
    return out
