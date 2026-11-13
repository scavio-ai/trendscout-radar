"""Optional AI briefing on top of the ranked report.

Works with any OpenAI-compatible chat completions endpoint (OpenAI, OpenRouter,
a local Ollama or LiteLLM proxy). Without LLM_API_KEY and LLM_MODEL the radar
skips this step; the ranked report is complete on its own.
"""

from __future__ import annotations

import json

import httpx

from .rank import Report

PROMPT = """You write a short daily trend briefing for marketers, founders and creators.
Use ONLY the data below. Do not invent numbers, names or events.

Write 4 to 6 bullet points in plain English:
- Lead with the user's topics: what is new for each one, and what it suggests they could act on today.
- Then anything trending on more than one platform, if it matters.
- Then one bullet on the most notable other new entries.
No preamble, no closing line, no emojis.

DATA:
{data}
"""


def _digest(report: Report) -> dict:
    return {
        "cross_platform": [{"term": s.title, "platforms": s.platforms} for s in report.cross[:8]],
        "new_by_source": {
            s.label: [e.item.title for e in s.entries if e.status == "new"][:5] for s in report.sections
        },
        "top_by_source": {s.label: [e.item.title for e in s.entries][:5] for s in report.sections},
        "topics": {
            tr.topic.name: {"new": [h.item.title for h in tr.hits if h.new][:5], "top": [h.item.title for h in tr.hits][:5]}
            for tr in report.topics
        },
    }


def summarize(report: Report, api_key: str, base_url: str, model: str, http: httpx.Client) -> str:
    resp = http.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT.format(data=json.dumps(_digest(report), ensure_ascii=False))}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return text.strip()
