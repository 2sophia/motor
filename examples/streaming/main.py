# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Live streaming — render the agent's output token-by-token.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py

What you'll see:
- a "thinking" block that fills in live (if the model thinks)
- a `[Read …]` indicator when the agent opens a tool_use
- live preview of the tool_use input as it streams in (file_path appearing
  before the model has finished sending the JSON envelope)
- a `[Read ✓]` line with a snippet of the tool_result
- the final answer streaming in as text_delta chunks

Compare against `examples/quickstart` which uses `motor.run()` and only
prints the final answer once everything is done.
"""
from __future__ import annotations

import asyncio
import sys

from sophia_motor import (
    Motor,
    MotorConfig,
    RunTask,
    DoneChunk,
    InitChunk,
    RunStartedChunk,
    TextDeltaChunk,
    TextBlockChunk,
    ThinkingDeltaChunk,
    ThinkingBlockChunk,
    ToolUseStartChunk,
    ToolUseDeltaChunk,
    ToolUseCompleteChunk,
    ToolUseFinalizedChunk,
    ToolResultChunk,
)

NOTE = """\
Acme Bank publishes quarterly threshold rates pursuant to article 2 of
the consumer credit regulation. The threshold rate for Q1 2026 is set at
12.5% for revolving consumer credit and 8.2% for fixed-instalment loans.
"""

# ANSI helpers — keep the example self-contained, no rich/click dep.
DIM = "\033[2m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def _w(s: str) -> None:
    """stdout write + flush — needed for chunk-by-chunk rendering."""
    sys.stdout.write(s)
    sys.stdout.flush()


async def main() -> None:
    motor = Motor(MotorConfig(console_log_enabled=False))

    task = RunTask(
        prompt=(
            "Read attachments/note.txt and tell me, in one sentence, "
            "what the two threshold rates are for Q1 2026."
        ),
        tools=["Read"],
        attachments={"note.txt": NOTE},
        max_turns=4,
    )

    in_thinking = False
    in_text = False

    async for chunk in motor.stream(task):
        if isinstance(chunk, RunStartedChunk):
            _w(f"{DIM}[run {chunk.run_id} • model={chunk.model}]{RESET}\n\n")

        elif isinstance(chunk, InitChunk):
            _w(f"{DIM}[session {chunk.session_id}]{RESET}\n")

        elif isinstance(chunk, ThinkingDeltaChunk):
            if not in_thinking:
                _w(f"{MAGENTA}thinking ▸ {RESET}")
                in_thinking = True
            _w(f"{MAGENTA}{chunk.text}{RESET}")

        elif isinstance(chunk, ThinkingBlockChunk):
            # Fallback path (no deltas were streamed). Print the whole block.
            _w(f"{MAGENTA}thinking ▸ {chunk.text}{RESET}\n")

        elif isinstance(chunk, ToolUseStartChunk):
            if in_thinking:
                _w("\n\n")
                in_thinking = False
            _w(f"{YELLOW}[{chunk.tool} …]{RESET} ")

        elif isinstance(chunk, ToolUseDeltaChunk):
            # Show the partial input as the model sends it. `extracted` is
            # already best-effort parsed for known tools.
            preview = chunk.extracted.get("file_path") or chunk.extracted.get("command") or ""
            if preview:
                _w(f"\r{YELLOW}[{chunk.tool} …]{RESET} {preview}     ")

        elif isinstance(chunk, ToolUseFinalizedChunk):
            # Authoritative input — overwrites whatever delta preview showed.
            args = chunk.input.get("file_path") or chunk.input.get("command") or "?"
            _w(f"\r{YELLOW}[{chunk.tool}]{RESET} {args}\n")

        elif isinstance(chunk, ToolUseCompleteChunk):
            # No-op for UI: ToolUseFinalizedChunk already painted the line.
            pass

        elif isinstance(chunk, ToolResultChunk):
            color = RED if chunk.is_error else GREEN
            mark = "✗" if chunk.is_error else "✓"
            snippet = chunk.preview.replace("\n", " ⏎ ")[:80]
            _w(f"{color}  {mark} {snippet}{RESET}\n\n")

        elif isinstance(chunk, TextDeltaChunk):
            if not in_text:
                _w(f"{CYAN}answer ▸ {RESET}")
                in_text = True
            _w(f"{CYAN}{chunk.text}{RESET}")

        elif isinstance(chunk, TextBlockChunk):
            # Fallback — no deltas streamed for this turn.
            _w(f"{CYAN}answer ▸ {chunk.text}{RESET}\n")

        elif isinstance(chunk, DoneChunk):
            r = chunk.result
            _w("\n\n" + "─" * 60 + "\n")
            _w(
                f"turns={r.metadata.n_turns}  "
                f"tools={r.metadata.n_tool_calls}  "
                f"tokens=in:{r.metadata.input_tokens}/out:{r.metadata.output_tokens}  "
                f"cost=${r.metadata.total_cost_usd:.4f}  "
                f"duration={r.metadata.duration_s:.1f}s\n"
            )
            if r.metadata.is_error:
                _w(f"{RED}error: {r.metadata.error_reason}{RESET}\n")

    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
