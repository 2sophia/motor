# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Skills — load a folder of skills and let the agent pick the right one.

Three skills are bundled in `skills_local/`:

  - say-hello       : the simplest possible instructional skill
  - python-math     : computes arithmetic by running `python -c` inline
  - apply-discount  : applies a proprietary tier-based discount table
                      that lives ONLY inside the bundled helper script
                      (`scripts/discount.py`). The agent cannot guess
                      the percentages — it must invoke the script via
                      Bash.

The motor symlinks each subdirectory of `skills_local/` (whichever has
a `SKILL.md`) under `<run>/.claude/skills/<name>/`. The model decides
which skill to invoke based on the user's prompt.

`python-math` shows on-the-fly Python execution (no helper script).
`apply-discount` shows the bundled-helper-script pattern: a skill that
ships its own Python code, executable by the agent through Bash via
the `$CLAUDE_CONFIG_DIR/skills/<name>/scripts/<file>.py` path.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


# Resolve the local skills folder relative to this file so the example
# is runnable from any cwd.
SKILLS_DIR = Path(__file__).parent / "skills_local"


async def main() -> None:
    # Singleton motor with shared defaults: Skill + Bash always available
    # so any of the three skills can run without per-task overrides.
    motor = Motor(MotorConfig(
        default_tools=["Skill", "Bash"],
        default_skills=SKILLS_DIR,
        default_max_turns=8,
    ))

    prompts = [
        ("say-hello",
         "Use the say-hello skill to greet me. The task is: review my Q1 OKRs."),
        ("python-math",
         "If a SaaS grew revenue from 240k to 318k in one quarter, what's the "
         "compound annual growth rate (CAGR) over a year (extrapolated)?"),
        ("apply-discount",
         "A GOLD-tier customer just placed a $1500 order. Apply our tier "
         "discount and tell me the final amount."),
    ]

    for label, prompt in prompts:
        print(f"\n══════ skill: {label} ══════")
        print(f"prompt: {prompt}\n")
        result = await motor.run(RunTask(prompt=prompt))
        if result.metadata.is_error:
            print(f"  ERROR: {result.metadata.error_reason}")
            continue
        print(f"answer:\n{result.output_text}\n")
        print(
            f"  ↳ turns={result.metadata.n_turns}  "
            f"tools={result.metadata.n_tool_calls}  "
            f"cost=${result.metadata.total_cost_usd:.4f}  "
            f"duration={result.metadata.duration_s:.1f}s"
        )

    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
