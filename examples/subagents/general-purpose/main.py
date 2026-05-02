# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Subagents — built-in `general-purpose`, no custom agent needed.

Sometimes you don't need specialists — you just want **context isolation**:
let the agent explore a folder / read many files / run several greps
WITHOUT all that content piling up in the main conversation.

When `Agent` is in `tools` and you don't define any custom agents, the
SDK exposes the built-in `general-purpose` subagent. The main agent can
delegate exploration to it; only the final summary returns to the parent.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


# Use the sophia_motor source itself as a non-trivial codebase to explore.
TARGET_DIR = Path(__file__).resolve().parents[3] / "src" / "sophia_motor"


async def main() -> None:
    # No custom agents — just expose the Agent tool. Whitelisting 'Agent'
    # in default_tools is enough: the motor's conflict-resolution removes
    # it from the default disallowed block automatically.
    motor = Motor(MotorConfig(
        default_tools=["Read", "Glob", "Grep", "Agent"],
        default_max_turns=15,
    ))

    result = await motor.run(RunTask(
        prompt=(
            f"Explore {TARGET_DIR}. Use a subagent to scan the codebase, "
            f"identify the three highest-level public entry points (the ones "
            f"a new user would call first), and return for each: name, file "
            f"path, and a one-line purpose. Keep your reply concise — the "
            f"subagent should do the digging, you only summarize."
        ),
    ))

    print("─" * 60)
    print(result.output_text)
    print("─" * 60)
    print(
        f"turns={result.metadata.n_turns}  "
        f"tools={result.metadata.n_tool_calls}  "
        f"cost=${result.metadata.total_cost_usd:.4f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
