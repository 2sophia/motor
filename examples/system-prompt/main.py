# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""System prompt — the cheapest way to shape the agent's voice.

One step up from the bare quickstart: still no tools, still no schema,
but the run carries a `system` that fixes the persona, the tone, and
the response shape. Same `Motor()` instance reused across calls; only
the `system` (and prompt) changes.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, RunTask


SOURCE_TEXT = """
Q1 telemetry shows a 23% increase in API latency on the search endpoint
after the rollout of the new ranking model. The new model improves
NDCG@10 by 4.2 points but adds ~80ms p99 to every request. The product
team is debating whether to keep, roll back, or stage a hybrid.
"""


async def main() -> None:
    motor = Motor()  # zero tools, zero schema — only system + prompt vary

    runs = [
        ("formal-analyst",
         "You are a senior research analyst. Reply in formal English. "
         "Structure: one-sentence headline, two bullets of evidence, one "
         "bullet of recommended action. No filler, no exclamation marks."),
        ("casual-friend",
         "You are explaining things to a friend over coffee. Casual tone, "
         "no jargon, ~3 short sentences. Pretend they don't work in tech."),
        ("ruthless-pm",
         "You are a product manager who just lost three weeks of velocity. "
         "Reply in ≤60 words. Make a decision (keep / roll back / hybrid) "
         "and justify it in one breath. No hedging."),
    ]

    for label, system in runs:
        print(f"\n══════ system: {label} ══════")
        result = await motor.run(RunTask(
            system=system,
            prompt=f"Summarize the situation and recommend a next step:\n\n{SOURCE_TEXT}",
            max_turns=2,
        ))
        if result.metadata.is_error:
            print(f"  ERROR: {result.metadata.error_reason}")
            continue
        print(result.output_text)
        print(
            f"  ↳ tokens=in:{result.metadata.input_tokens}/out:{result.metadata.output_tokens}  "
            f"cost=${result.metadata.total_cost_usd:.4f}  "
            f"duration={result.metadata.duration_s:.1f}s"
        )


if __name__ == "__main__":
    asyncio.run(main())
