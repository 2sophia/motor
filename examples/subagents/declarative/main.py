# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Subagents — declarative pattern.

Define a few subagents up-front on `MotorConfig.default_agents`. The model
reads each subagent's `description` and decides which one to invoke based
on the prompt — no explicit "use the X agent" needed.

This is the natural fit for chat backends and orchestrators where the
caller doesn't know in advance which specialist will be relevant.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sophia_motor import (
    AgentDefinition,
    Motor,
    MotorConfig,
    RunTask,
)


# A small file we'll have the agents look at.
SAMPLE_DIR = Path(__file__).parent / "files"


async def main() -> None:
    motor = Motor(MotorConfig(
        # Two specialists. The model picks based on `description` + prompt.
        default_agents={
            "code-reviewer": AgentDefinition(
                description=(
                    "Expert code reviewer. Use for quality, style, security, and "
                    "maintainability checks. Returns a concise list of findings."
                ),
                prompt=(
                    "You are a senior code reviewer. For the file you receive, "
                    "list at most three concrete improvements: title + one-line "
                    "rationale each. Prioritize correctness over style."
                ),
                tools=["Read", "Grep", "Glob"],
            ),
            "doc-checker": AgentDefinition(
                description=(
                    "Documentation auditor. Use to verify that comments and "
                    "docstrings match the code they describe."
                ),
                prompt=(
                    "You are a documentation auditor. Check whether docstrings "
                    "and inline comments accurately describe the surrounding "
                    "code. Report mismatches; ignore code quality issues — "
                    "those go to the code-reviewer."
                ),
                tools=["Read", "Grep"],
            ),
        },
        # Opt-in: 'Agent' tool reachable + nothing in disallowed.
        default_tools=["Read", "Grep", "Glob", "Agent"],
        default_disallowed_tools=[],
        default_max_turns=10,
    ))

    # The agent decides which subagent to spawn based on the prompt.
    result = await motor.run(RunTask(
        prompt=(
            f"Look at the Python files under {SAMPLE_DIR}. First check whether "
            f"the docstrings still match the code, then review code quality. "
            f"Return one section per subagent's findings."
        ),
    ))

    print("─" * 60)
    print(result.output_text)
    print("─" * 60)
    print(
        f"turns={result.metadata.n_turns}  "
        f"tools={result.metadata.n_tool_calls}  "
        f"cost=${result.metadata.total_cost_usd:.4f}  "
        f"duration={result.metadata.duration_s:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
