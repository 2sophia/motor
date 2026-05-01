# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Interactive console — pre-configured motor + REPL.

⚠️  This example needs the [console] extras (rich + prompt-toolkit).
    Install with:
        pip install sophia-motor[console]

What you get when you run it: a chat-like terminal where you type a
prompt, watch the agent's thinking / tool calls / file outputs stream
live, then type the next prompt. The motor is pre-configured with
tools/attachments/system, so every prompt has them ready. Slash
commands (/help, /files, /audit, /clear, /exit) are autocompleted.
Ctrl+C interrupts a running task without quitting the console;
Ctrl+D quits.

Run:
    pip install sophia-motor[console]
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, MotorConfig


SAMPLE_DATA = """\
sales-2026-q1.json
sales-2026-q2.json
sales-2026-q3.json
notes/onboarding.md
notes/pricing.md
"""


async def main() -> None:
    motor = Motor(MotorConfig(
        # The console uses every default_* you set here. The user just
        # types prompts; the motor runs them with this config every turn.
        default_system=(
            "You are a concise data analyst. Use the tools to investigate "
            "the attachments before answering. Keep responses tight."
        ),
        default_tools=["Read", "Glob", "Write"],
        default_attachments={
            "index.txt": SAMPLE_DATA,
            "sales-2026-q1.json": '{"revenue": 102000, "units": 1234}',
            "sales-2026-q2.json": '{"revenue": 118500, "units": 1410}',
            "sales-2026-q3.json": '{"revenue": 134200, "units": 1602}',
        },
        default_max_turns=8,
    ))

    # Try prompting things like:
    #   What files are in attachments/?
    #   Read each sales JSON and tell me the trend.
    #   Write outputs/summary.md with the totals.
    # Then `/files` to see what got generated, `/audit` to find the dumps.
    await motor.console()


if __name__ == "__main__":
    asyncio.run(main())
