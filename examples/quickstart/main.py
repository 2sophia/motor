# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Minimal quickstart — one prompt, one motor, one answer.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, RunTask


async def main() -> None:
    motor = Motor()  # default config; reads ANTHROPIC_API_KEY from env or ./.env

    result = await motor.run(RunTask(
        prompt="Explain in two sentences what makes a good API design.",
        max_turns=2,
    ))

    print("─" * 60)
    print(f"output:\n{result.output_text}")
    print("─" * 60)
    print(
        f"turns={result.metadata.n_turns}  "
        f"tokens=in:{result.metadata.input_tokens}/out:{result.metadata.output_tokens}  "
        f"cost=${result.metadata.total_cost_usd:.4f}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
