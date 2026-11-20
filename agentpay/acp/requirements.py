"""Deterministic buyer-requirement extraction for the ACP clarification loop.

The merchant needs category, budget and (for sized goods) size before it can
make a firm offer; anything missing produces a clarifying question instead of
a guess. An LLM extractor can be plugged in front, but the deterministic path
is what's tested and what the gateway trusts.
"""

from __future__ import annotations

import re
from typing import Any

CATEGORIES = {
    "running": ("running", "run", "marathon", "jog", "road"),
    "trail": ("trail", "hike", "hiking"),
    "basketball": ("basketball", "court", "hoop"),
    "apparel": ("shirt", "tee", "socks", "apparel"),
    "travel": ("flight", "fly", "plane"),
    "lodging": ("hotel", "stay", "night", "room"),
    "dining": ("dinner", "restaurant", "omakase", "table"),
}
SIZED = {"running", "trail", "basketball", "apparel"}

_BUDGET = re.compile(r"(?:under|below|less than|max|budget(?: of)?|up to|<)?\s*\$\s?(\d{1,6}(?:\.\d{1,2})?)", re.I)
_SIZE = re.compile(r"\bsize\s*([0-9]{1,2}(?:\.5)?|xs|s|m|l|xl)\b", re.I)


def extract(text: str) -> dict[str, Any]:
    t = text.lower()
    out: dict[str, Any] = {}
    if m := _BUDGET.search(text):
        out["max_price"] = int(round(float(m.group(1)) * 100))
    if m := _SIZE.search(text):
        out["size"] = m.group(1).upper() if m.group(1).isalpha() else m.group(1)
    for cat, words in CATEGORIES.items():
        if any(re.search(rf"\b{w}\b", t) for w in words):
            out["category"] = cat
            break
    return out


def merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    return {**base, **{k: v for k, v in patch.items() if v is not None}}


def missing(req: dict[str, Any]) -> list[str]:
    need = [f for f in ("category", "max_price") if f not in req]
    if req.get("category") in SIZED and "size" not in req:
        need.append("size")
    return need


QUESTIONS = {
    "category": "What are you shopping for (e.g. running shoes, trail shoes, a flight, a hotel)?",
    "max_price": "What's your budget?",
    "size": "What size?",
}


def question(fields: list[str]) -> str:
    return " ".join(QUESTIONS[f] for f in fields)
