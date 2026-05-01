# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Concurrency — one Motor, N runs in parallel.

The same Motor instance can drive any number of concurrent runs: the
proxy multiplexes them via per-run path prefixes (`/run/<id>/v1/messages`)
so each run owns its own audit dump and request counter without
serialization.

This is the canonical fan-out pattern for serving multiple users from
the same process — exactly how a chat backend (sophia-agent style)
spawns one agent run per HTTP request without instantiating one Motor
per user.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import BaseModel

from sophia_motor import Motor, MotorConfig, RunTask


class ToneVerdict(BaseModel):
    tone: Literal["positive", "neutral", "negative", "mixed"]
    one_line_reason: str


REVIEWS = [
    "Honestly the best support I've ever had — they refunded me in 2 minutes flat.",
    "It works. Documentation is sparse but the API is intuitive enough.",
    "Crashed three times in the first hour. Asking for a refund.",
    "Love the product, hate the pricing model. Great UX though.",
    "Setup took 4 minutes. Already replacing two other tools we used.",
]


async def classify(motor: Motor, review: str) -> tuple[str, ToneVerdict]:
    """Single classification — same `motor` instance shared across calls."""
    result = await motor.run(RunTask(
        system="You are a concise tone-classification agent.",
        prompt=f"Classify the tone of this review:\n\n{review}",
        output_schema=ToneVerdict,
        max_turns=2,
    ))
    if result.metadata.is_error:
        raise RuntimeError(result.metadata.error_reason)
    return review, result.output_data  # type: ignore[return-value]


async def main() -> None:
    # ONE motor — shared by all concurrent tasks. The proxy and the
    # CLAUDE_CONFIG_DIR machinery are reused; only per-run state
    # (audit dir, run_id, ClaudeSDKClient) is unique per task.
    motor = Motor(MotorConfig(console_log_enabled=False))

    t0 = time.monotonic()
    results = await asyncio.gather(*[
        classify(motor, review) for review in REVIEWS
    ])
    wall = time.monotonic() - t0

    await motor.stop()

    print("─" * 70)
    for review, verdict in results:
        print(f"  {verdict.tone:>8s}  · {review[:60]}…")
        print(f"           ↳ {verdict.one_line_reason}")
    print("─" * 70)
    print(f"classified {len(results)} reviews in {wall:.1f}s wall-clock "
          f"using ONE Motor instance — all {len(REVIEWS)} runs in parallel")


if __name__ == "__main__":
    asyncio.run(main())
