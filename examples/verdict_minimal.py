# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Minimal verdict example — the canonical happy path for sophia-motor.

A single `motor` is instantiated ONCE at module level and reused for N
tasks that differ only in the `prompt`. The proxy lazy-auto-starts on
the first `motor.run()` (no `async with`, no lifecycle ceremony).

Run:
  ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python examples/verdict_minimal.py
"""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from sophia_motor import Motor, MotorConfig, RunTask


# ── 1) Output schema (any Pydantic BaseModel works). ────────────────────
class Verdict(BaseModel):
    coverage: Literal["HIGH", "MEDIUM", "LOW"]
    rationale: str = Field(min_length=20)
    sub_requirements_covered: list[str]
    sub_requirements_uncovered: list[str]


# ── 2) Instantiate Motor ONCE at module level with shared defaults.
#    Any async function in the project can reuse it.
#    No `async with`: the proxy starts on the first `motor.run()`.
motor = Motor(MotorConfig(
    default_system="You are a compliance officer evaluating control coverage.",
    default_output_schema=Verdict,
    default_tools=[],            # pure reasoning, no tools by default
    default_allowed_tools=[],
    default_max_turns=5,
))


# ── 3) A "smart function" is a normal Python async def that builds a
#    RunTask filling in only the parts that vary (here: the prompt).
async def assess_obligation(obligation_text: str, controls: list[str]) -> Verdict:
    """Evaluate whether an obligation is covered by a set of candidate controls."""
    task = RunTask(
        prompt=(
            f"Evaluate whether the obligation is covered by the candidate "
            f"controls. Decompose into sub-requirements, quote verbatim, "
            f"produce a coverage verdict.\n\n"
            f"OBLIGATION:\n{obligation_text}\n\n"
            f"CANDIDATE CONTROLS:\n"
            + "\n".join(f"- {c}" for c in controls)
        ),
        # No system/tools/output_schema/max_turns here — taken from
        # MotorConfig defaults above. Override per-task if needed.
    )
    result = await motor.run(task)
    if result.metadata.is_error:
        raise RuntimeError(f"verdict failed: {result.metadata.error_reason}")
    return result.output_data  # type: ignore[return-value]


async def main() -> None:
    # First call: the proxy boots transparently (~500ms, one time).
    v1 = await assess_obligation(
        obligation_text=(
            "The control body must verify, within 30 days of publication, "
            "any breach of the regulatory threshold rates."
        ),
        controls=[
            "CTRL-001: Quarterly threshold-rate verification (Risk Mgmt)",
            "CTRL-042: Annual compliance audit (Internal Audit)",
        ],
    )
    print(f"=== Verdict 1 ===")
    print(f"  coverage      : {v1.coverage}")
    print(f"  rationale     : {v1.rationale}")
    print(f"  covered       : {v1.sub_requirements_covered}")
    print(f"  not covered   : {v1.sub_requirements_uncovered}")

    # Second call: proxy already alive, dispatch is immediate.
    v2 = await assess_obligation(
        obligation_text=(
            "The bank must publish threshold rates on its public website "
            "on a quarterly basis."
        ),
        controls=[
            "CTRL-100: Monthly publication on the internal intranet",
        ],
    )
    print(f"\n=== Verdict 2 ===")
    print(f"  coverage      : {v2.coverage}")
    print(f"  rationale     : {v2.rationale}")

    # Optional explicit cleanup — the proxy dies anyway when the Python
    # process terminates.
    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
