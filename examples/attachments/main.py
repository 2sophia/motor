# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Attachments — point the agent at a real folder of documents.

`RunTask.attachments` accepts a single value or a list. Items can be:

  1. Path / str  →  real FILE        → symlinked into <run>/attachments/<name>
  2. Path / str  →  real DIRECTORY   → mirrored as a real-tree of
                                       file-level symlinks under
                                       <run>/attachments/<dirname>/
  3. dict[str, str]                  → inline contents, written to disk

This example uses form 2: a directory of two arXiv PDFs is handed to
the agent. The agent discovers the files with Glob, reads each one
with Read (which understands PDFs natively), and produces a structured
side-by-side comparison.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sophia_motor import Motor, RunTask


# Directory bundled with this example. Drop additional PDFs / .md / .txt
# files in here and the prompt below will see them automatically.
FILES_DIR = Path(__file__).parent / "files"


async def main() -> None:
    motor = Motor()

    result = await motor.run(RunTask(
        prompt=(
            "Inside `attachments/files/` you'll find a small collection of "
            "research papers. For every paper produce a one-paragraph "
            "summary stating the problem, method, and main result. End "
            "with a short comparison: what do the papers have in common, "
            "and where do they differ?"
        ),
        tools=["Read", "Glob"],
        attachments=FILES_DIR,
        max_turns=12,
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
    print(f"audit:     {result.audit_dir}")


if __name__ == "__main__":
    asyncio.run(main())
