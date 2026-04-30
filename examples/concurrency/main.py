# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Concurrency — N motors running in parallel.

A single Motor instance handles one run at a time (its proxy is bound
to the active run for audit-dump tagging). For real parallelism you
instantiate N Motor objects and dispatch them with `asyncio.gather`.
Each motor gets its own kernel-assigned proxy port — no port
collisions, no shared state.

This is the canonical fan-out pattern: classify N items in parallel,
join the results, return.

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


async def classify_one(motor: Motor, review: str) -> tuple[str, ToneVerdict]:
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
    # One motor per concurrent task; each owns its proxy port.
    motors = [Motor(MotorConfig(console_log_enabled=False)) for _ in REVIEWS]

    t0 = time.monotonic()
    results = await asyncio.gather(*[
        classify_one(motor, review) for motor, review in zip(motors, REVIEWS)
    ])
    wall = time.monotonic() - t0

    # Cleanup — proxies stop, ports released. Optional: process exit
    # would do this for us, but it's tidy.
    await asyncio.gather(*[motor.stop() for motor in motors])

    print("─" * 70)
    for review, verdict in results:
        print(f"  {verdict.tone:>8s}  · {review[:60]}…")
        print(f"           ↳ {verdict.one_line_reason}")
    print("─" * 70)
    print(f"classified {len(results)} reviews in {wall:.1f}s wall-clock "
          f"using {len(motors)} parallel motors")


if __name__ == "__main__":
    asyncio.run(main())
