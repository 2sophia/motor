# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Interrupt an in-flight run — the "user clicks stop" pattern.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py

What it shows:
- a long task (read 3 files, then write a multi-paragraph analysis)
- a parallel task that calls `motor.interrupt()` after a couple of seconds
- the stream finishes cleanly with a terminal `DoneChunk` whose
  `result.metadata.was_interrupted=True` (and `is_error=False` — an
  interrupt is a deliberate user action, not a failure)
- partial output preserved: the audit dump under `<run>/audit/` keeps
  every API request/response up to the point of interruption

`motor.interrupt()` is distinct from `motor.stop()`:
- `stop()` shuts the motor down (dies the proxy, no more runs)
- `interrupt()` aborts the run currently in flight; the motor stays alive
  and you can fire another run after it
"""
from __future__ import annotations

import asyncio
import sys

from sophia_motor import (
    Motor,
    MotorConfig,
    RunTask,
    DoneChunk,
    RunStartedChunk,
    TextDeltaChunk,
    ToolUseStartChunk,
    ToolUseFinalizedChunk,
    ToolResultChunk,
)

NOTE_A = "Acme rates Q1-Q4 2026: 12.5%, 10.8%, 11.0%, 9.7%"
NOTE_B = "Beta rates Q1-Q4 2026: 14.2%, 13.1%, 13.5%, 12.0%"
NOTE_C = "Gamma rates Q1-Q4 2026: 8.0%, 7.7%, 7.5%, 7.2%"

CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"


def _w(s: str) -> None:
    sys.stdout.write(s); sys.stdout.flush()


async def main() -> None:
    motor = Motor(MotorConfig(console_log_enabled=False))

    task = RunTask(
        prompt=(
            "Read every .txt file under attachments/ one by one (use Glob "
            "to find them). Then write a long, detailed comparative "
            "analysis with at least five paragraphs covering trends, "
            "outliers, and policy implications."
        ),
        tools=["Glob", "Read"],
        attachments={"a.txt": NOTE_A, "b.txt": NOTE_B, "c.txt": NOTE_C},
        max_turns=10,
    )

    in_text = False
    final_run_id = None

    async def consume() -> None:
        nonlocal in_text, final_run_id
        async for chunk in motor.stream(task):
            if isinstance(chunk, RunStartedChunk):
                final_run_id = chunk.run_id
                _w(f"{DIM}[run started • {chunk.run_id}]{RESET}\n")
            elif isinstance(chunk, ToolUseStartChunk):
                _w(f"{YELLOW}[{chunk.tool} …]{RESET}")
            elif isinstance(chunk, ToolUseFinalizedChunk):
                args = chunk.input.get("file_path") or chunk.input.get("pattern") or "?"
                _w(f"\r{YELLOW}[{chunk.tool}]{RESET} {args}\n")
            elif isinstance(chunk, ToolResultChunk):
                snippet = chunk.preview.replace("\n", " ⏎ ")[:60]
                _w(f"{GREEN}  ✓ {snippet}{RESET}\n")
            elif isinstance(chunk, TextDeltaChunk):
                if not in_text:
                    _w(f"\n{CYAN}analysis ▸ {RESET}")
                    in_text = True
                _w(f"{CYAN}{chunk.text}{RESET}")
            elif isinstance(chunk, DoneChunk):
                _w("\n" + "─" * 60 + "\n")
                m = chunk.result.metadata
                tag = (
                    f"{YELLOW}INTERRUPTED{RESET}" if m.was_interrupted
                    else (f"{RED}ERROR{RESET}" if m.is_error else f"{GREEN}OK{RESET}")
                )
                _w(
                    f"status={tag}  "
                    f"interrupted={m.was_interrupted}  "
                    f"is_error={m.is_error}  "
                    f"turns={m.n_turns}  "
                    f"tools={m.n_tool_calls}  "
                    f"cost=${m.total_cost_usd:.4f}\n"
                )
                _w(f"audit dump preserved at: {chunk.result.audit_dir}\n")

    async def trigger_interrupt_after(delay: float) -> None:
        """Fire the interrupt from a parallel task — same pattern as a UI
        "stop" button on a different request handler."""
        await asyncio.sleep(delay)
        _w(f"\n{RED}>>> motor.interrupt() called by external task{RESET}\n")
        # Pass `final_run_id` (when known) as the safety check — this is
        # how a UI avoids interrupting the wrong run if the user clicked
        # stop right as a previous run finished and a new one started.
        ok = await motor.interrupt(run_id=final_run_id)
        _w(f"{RED}>>> interrupt acknowledged: {ok}{RESET}\n")

    consumer = asyncio.create_task(consume())
    interrupter = asyncio.create_task(trigger_interrupt_after(5.0))
    await asyncio.gather(consumer, interrupter)
    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
