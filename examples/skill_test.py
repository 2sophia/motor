# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Test skill: 'say-hello' — verifies that skill linking works end-to-end.

Setup:
  - skill source: examples/skills_example/say_hello/SKILL.md
  - the motor links it under <run>/.claude/skills/say_hello/
  - prompt: "invoke the say-hello skill on my task: <task>"
  - expected output: "HELLO WORLD 👋\\n<summary>"

Run:
  ANTHROPIC_API_KEY=... .venv/bin/python examples/skill_test.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


SKILLS_DIR = Path(__file__).parent / "skills_example"


async def main() -> None:
    async with Motor(MotorConfig()) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Invoke the `say-hello` skill on my task: "
                "'read the file summary.md and prepare a report'."
            ),
            system=(
                "You can invoke skills via the Skill tool. The 'say-hello' "
                "skill is available — use it for greeting tasks."
            ),
            tools=["Skill"],
            allowed_tools=["Skill"],
            skills=SKILLS_DIR,
            max_turns=5,
        ))

    print("\n" + "=" * 60)
    print(f"run_id     : {result.run_id}")
    print(f"is_error   : {result.metadata.is_error}")
    print(f"turns      : {result.metadata.n_turns}")
    print(f"tool_calls : {result.metadata.n_tool_calls}")
    print(f"cost       : ${result.metadata.total_cost_usd:.4f}")
    print(f"duration   : {result.metadata.duration_s:.1f}s")
    print("=" * 60)
    print(f"\nOUTPUT\n{result.output_text or '(empty)'}\n")
    print("=" * 60)
    print("Checks:")
    starts_ok = (result.output_text or "").lstrip().startswith("HELLO WORLD")
    print(f"  output starts with 'HELLO WORLD'?  {'✓' if starts_ok else '✗'}")
    print(f"  audit dir: {result.audit_dir}")
    print(f"  linked skill: {result.workspace_dir / '.claude' / 'skills' / 'say_hello'}")


if __name__ == "__main__":
    asyncio.run(main())
