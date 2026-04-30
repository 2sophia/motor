# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Standalone smoke test (non-pytest).

Requires the package to be installed in the active environment:

    pip install -e ".[dev]"
    ANTHROPIC_API_KEY=sk-ant-... python tests/run_smoke.py

You will see the live event/log stream on stdout and an audit dir under
./.runs/<run_id>/ when the run completes.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

try:
    from sophia_motor import Motor, MotorConfig, RunTask
except ModuleNotFoundError as exc:
    print(
        f"ERROR: {exc}\n"
        "       Install the package first: pip install -e \".[dev]\"",
        file=sys.stderr,
    )
    raise SystemExit(2)


SAMPLE_TEXT = """\
Acme Bank publishes quarterly threshold rates pursuant to article 2 of
the relevant consumer credit regulation. The threshold rate for Q1 2026
is set at 12.5% for consumer credit products. Internal policy PR-0007
requires contract updates within 15 days of decree publication.
"""


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY is not set.\n"
            "       Export the key before running the smoke test."
        )
        return 2

    config = MotorConfig(
        workspace_root=Path("./.runs"),
        console_log_enabled=True,
    )

    print("\n=== sophia-motor smoke test ===\n")

    async with Motor(config) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Read the file `attachments/sample.txt` (path relative to "
                "your working directory) and produce a brief two-sentence "
                "summary of the regulatory content cited. Reply in English."
            ),
            system=(
                "You are a compliance reasoning agent. All file paths you use "
                "MUST be relative to the current working directory. Never use "
                "absolute paths."
            ),
            tools=["Read"],
            allowed_tools=["Read"],
            attachments=[{"sample.txt": SAMPLE_TEXT}],
            max_turns=5,
        ))

    print("\n=== RESULT ===")
    print(f"run_id:      {result.run_id}")
    print(f"is_error:    {result.metadata.is_error}")
    print(f"turns:       {result.metadata.n_turns}")
    print(f"tool_calls:  {result.metadata.n_tool_calls}")
    print(f"tokens:      in={result.metadata.input_tokens} "
          f"out={result.metadata.output_tokens}")
    print(f"cost:        ${result.metadata.total_cost_usd:.4f}")
    print(f"duration:    {result.metadata.duration_s:.1f}s")
    print(f"audit dir:   {result.audit_dir}")
    print(f"workspace:   {result.workspace_dir}")
    print(f"\nOutput:\n{result.output_text or '(none)'}\n")

    return 0 if not result.metadata.is_error else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
