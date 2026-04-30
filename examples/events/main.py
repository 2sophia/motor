# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Event hooks — observe every turn the agent takes.

The Motor exposes two streams:

  - on_event  → structured Event objects (run_started, tool_use,
                tool_result, assistant_text, thinking, proxy_request,
                proxy_response, result)
  - on_log    → leveled LogRecord objects (DEBUG/INFO/WARNING/ERROR)

Subscribers can be sync or async. Errors raised inside a subscriber are
caught and logged — they never kill the run.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from collections import Counter

from sophia_motor import Motor, MotorConfig, RunTask


async def main() -> None:
    # console_log_enabled=False so the example owns stdout.
    motor = Motor(MotorConfig(console_log_enabled=False))

    event_counts: Counter[str] = Counter()
    tool_uses: list[str] = []

    @motor.on_event
    async def on_event(event):
        event_counts[event.type] += 1
        if event.type == "tool_use":
            name = event.payload.get("tool", "?")
            tool_uses.append(name)
            print(f"  · tool_use     → {name}")
        elif event.type == "tool_result":
            ok = "✓" if not event.payload.get("is_error") else "✗"
            print(f"  · tool_result  {ok}")
        elif event.type == "assistant_text":
            preview = event.payload.get("preview", "").replace("\n", " ")
            print(f"  · assistant    “{preview[:80]}…”")
        elif event.type == "result":
            cost = event.payload.get("cost_usd", 0.0)
            print(f"  · result       cost=${cost:.4f}")

    @motor.on_log
    async def on_log(record):
        if record.level == "WARNING" or record.level == "ERROR":
            print(f"  ! {record.level}: {record.message}")

    print("─ run start ──────────────────────────────────────────────")
    result = await motor.run(RunTask(
        prompt=(
            "Read attachments/notes.md, count how many bullet points "
            "it contains, and return that number along with a short summary."
        ),
        tools=["Read"],
        attachments=[{
            "notes.md": (
                "# Project notes\n\n"
                "- Investigate the latency regression on the search endpoint\n"
                "- Roll out the new caching layer to staging\n"
                "- Schedule a security review for the auth refactor\n"
                "- Update the dashboards after the metric rename\n"
            )
        }],
        max_turns=5,
    ))
    print("─ run end ────────────────────────────────────────────────")
    print(f"\nfinal answer:\n{result.output_text}\n")
    print(f"event histogram: {dict(event_counts)}")
    print(f"tools used     : {tool_uses}")


if __name__ == "__main__":
    asyncio.run(main())
