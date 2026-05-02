# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Subagents — explicit invocation by name.

Same setup as the declarative pattern, but the prompt names the subagent
explicitly: "Use the security-reviewer agent to ...". This bypasses the
model's automatic routing and forces the delegation. Useful when:

  - You know which specialist must run (don't trust the routing)
  - You're chaining specialists deterministically in an orchestrator
  - You want to reproduce the same delegation across calls

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio

from sophia_motor import (
    AgentDefinition,
    Motor,
    MotorConfig,
    RunTask,
)


SAMPLE_CODE = """
def authenticate(user_input):
    # quick auth check
    query = "SELECT * FROM users WHERE name = '" + user_input + "'"
    return execute(query)
""".strip()


async def main() -> None:
    motor = Motor(MotorConfig(
        default_agents={
            "security-reviewer": AgentDefinition(
                description=(
                    "Strict security reviewer. Use when the prompt explicitly "
                    "asks for a security audit or when input comes from "
                    "untrusted sources."
                ),
                prompt=(
                    "You are a strict security reviewer. For the snippet you "
                    "receive, list every concrete vulnerability (CWE class + "
                    "one-line explanation). Be specific; do not invent issues "
                    "to pad the list."
                ),
                tools=["Read"],
            ),
        },
        default_tools=["Read", "Agent"],
        default_disallowed_tools=[],
    ))

    # The prompt names the subagent — the main agent is forced to delegate.
    result = await motor.run(RunTask(
        prompt=(
            "Use the security-reviewer agent to audit this Python snippet "
            f"for vulnerabilities:\n\n```python\n{SAMPLE_CODE}\n```\n\n"
            "Return the subagent's findings verbatim."
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
