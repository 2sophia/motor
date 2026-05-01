# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""File creation — the agent writes outputs you can persist anywhere.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py

What you'll see:
- live `[Write] outputs/<file>` indicators as the agent commits each file
- a final list of `result.output_files` with size + mime + ext
- the files copied into `./generated/` to demonstrate the persist pattern

⚠️  The run workspace is transient. `motor.clean_runs()` (or any cron
sweep / container teardown / volatile workspace_root) will wipe the
files. Persist what you care about with `output.copy_to(...)` BEFORE
relying on the path again. The audit dump under `<run>/audit/` keeps
the API call trail; `<run>/agent_cwd/outputs/` does not.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sophia_motor import (
    Motor,
    MotorConfig,
    RunTask,
    DoneChunk,
    OutputFileReadyChunk,
    RunStartedChunk,
    TextDeltaChunk,
    ToolUseFinalizedChunk,
)

# Synthetic data the agent will turn into a report. In real use this
# would be an attachment from disk, a DB query result, etc.
SAMPLE_DATA = json.dumps([
    {"product": "alpha", "units_sold": 1240, "revenue_eur": 18600},
    {"product": "beta",  "units_sold":  870, "revenue_eur": 13050},
    {"product": "gamma", "units_sold": 2103, "revenue_eur": 31545},
    {"product": "delta", "units_sold":  410, "revenue_eur":  6150},
    {"product": "epsil", "units_sold": 1789, "revenue_eur": 26835},
], indent=2)

PERSIST_DIR = Path("./generated")

CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _w(s: str) -> None:
    sys.stdout.write(s); sys.stdout.flush()


async def main() -> None:
    motor = Motor(MotorConfig(console_log_enabled=False))

    task = RunTask(
        prompt=(
            "Read attachments/sales.json. Then write two files:\n"
            "  1. outputs/report.md — a 4-line markdown report listing the "
            "top 3 products by revenue, with EUR amounts.\n"
            "  2. outputs/summary.json — a JSON object with three fields: "
            "total_units (int), total_revenue_eur (int), top_product (str).\n"
            "Use the Write tool for both. No commentary; just write the files."
        ),
        tools=["Read", "Write"],
        attachments={"sales.json": SAMPLE_DATA},
        max_turns=6,
    )

    in_text = False

    async for chunk in motor.stream(task):
        if isinstance(chunk, RunStartedChunk):
            _w(f"{DIM}[run {chunk.run_id}]{RESET}\n\n")

        elif isinstance(chunk, ToolUseFinalizedChunk):
            tool = chunk.tool
            args = (
                chunk.input.get("file_path")
                or chunk.input.get("pattern")
                or "?"
            )
            _w(f"{YELLOW}[{tool}]{RESET} {args}\n")

        elif isinstance(chunk, OutputFileReadyChunk):
            # Live signal: the file is now on disk in the run workspace.
            _w(f"{GREEN}  ✓ output ready: {chunk.relative_path}{RESET}\n")

        elif isinstance(chunk, TextDeltaChunk):
            if not in_text:
                _w(f"\n{CYAN}note ▸ {RESET}")
                in_text = True
            _w(f"{CYAN}{chunk.text}{RESET}")

        elif isinstance(chunk, DoneChunk):
            r = chunk.result
            _w("\n\n" + "─" * 60 + "\n")
            _w(f"{BOLD}generated files{RESET} ({len(r.output_files)})\n")
            for f in r.output_files:
                _w(f"  • {f.relative_path}  "
                   f"{DIM}size={f.size}b  mime={f.mime}  ext={f.ext}{RESET}\n")

            # Persist them so they survive `clean_runs()`. The destination
            # could just as easily be S3, a blob store, a DB BLOB column,
            # etc. — `copy_to(Path)` is the local-filesystem case; the
            # generic primitive is `read_bytes()` which you can hand to
            # any storage client.
            PERSIST_DIR.mkdir(exist_ok=True)
            _w(f"\n{BOLD}persisting → {PERSIST_DIR.resolve()}{RESET}\n")
            for f in r.output_files:
                dest = f.copy_to(PERSIST_DIR)
                _w(f"  → {dest}\n")

            # Show what the model actually wrote
            _w(f"\n{BOLD}contents{RESET}\n")
            for f in r.output_files:
                _w(f"\n{DIM}── {f.relative_path} ──{RESET}\n")
                _w(f.read_text())
                _w("\n")

            _w("\n" + "─" * 60 + "\n")
            _w(
                f"turns={r.metadata.n_turns}  "
                f"tools={r.metadata.n_tool_calls}  "
                f"cost=${r.metadata.total_cost_usd:.4f}  "
                f"duration={r.metadata.duration_s:.1f}s\n"
            )

    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
