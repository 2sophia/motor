# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Web search + fetch — opt in to the live-internet tools.

`WebSearch` and `WebFetch` are blocked by default for safety. Pass them
explicitly in `tools=[...]` and the motor's conflict-resolution drops
them from the resolved disallowed set so the agent can actually use
them.

The agent here picks a topic, searches the web, fetches one or two
result pages, and produces a typed brief.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from sophia_motor import Motor, RunTask


class Source(BaseModel):
    title: str
    url: str
    one_line_takeaway: str = Field(min_length=10, max_length=200)


class WebBrief(BaseModel):
    topic: str
    sources: list[Source] = Field(min_length=2, max_length=4)
    short_summary: str = Field(min_length=40)


async def main() -> None:
    motor = Motor()

    result = await motor.run(RunTask(
        prompt=(
            "Topic: 'state of WebGPU support in browsers in 2026'.\n\n"
            "Search the web, pick 2 to 4 reputable sources, fetch each one, "
            "and produce a short brief. Cite the URL of every source."
        ),
        tools=["WebSearch", "WebFetch"],
        output_schema=WebBrief,
        max_turns=10,
    ))

    if result.metadata.is_error:
        raise SystemExit(f"run failed: {result.metadata.error_reason}")

    brief: WebBrief = result.output_data  # type: ignore[assignment]

    print("─" * 70)
    print(f"topic   : {brief.topic}")
    print(f"summary : {brief.short_summary}")
    print("─" * 70)
    for i, s in enumerate(brief.sources, 1):
        print(f"[{i}] {s.title}")
        print(f"    {s.url}")
        print(f"    → {s.one_line_takeaway}")
    print("─" * 70)
    print(
        f"turns={result.metadata.n_turns}  "
        f"tools={result.metadata.n_tool_calls}  "
        f"cost=${result.metadata.total_cost_usd:.4f}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
