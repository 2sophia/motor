# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Python tools — define functions, pass them, the model calls them.

This example shows the three flavours that cover ~95% of real use:
  1. A pure data tool (no filesystem, no ctx) — `fetch_user`
  2. A tool that writes into the run's outputs/ via ToolContext — `write_report`
  3. A sync function (auto-wrapped) — `hash_payload`

Run:
    pip install sophia-motor
    echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
    python examples/python-tools/main.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel

from sophia_motor import (
    Motor,
    MotorConfig,
    RunTask,
    ToolContext,
    tool,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Pure data tool — no filesystem, no ctx.
# ─────────────────────────────────────────────────────────────────────

class FetchUserInput(BaseModel):
    user_id: int

class FetchUserOutput(BaseModel):
    name: str
    email: str
    role: str


# A toy in-memory directory the tool reads from.
_USERS = {
    1:  ("Alex",   "alex@example.com",   "ceo"),
    2:  ("Eco",    "eco@example.com",    "engineer"),
    42: ("Trillian", "tril@example.com", "operator"),
}


@tool
async def fetch_user(args: FetchUserInput) -> FetchUserOutput:
    """Fetch a user record from the internal directory by integer ID.

    Returns name, email, and role. Raises if the ID is not registered.
    """
    rec = _USERS.get(args.user_id)
    if rec is None:
        raise KeyError(f"user_id {args.user_id} not found")
    name, email, role = rec
    return FetchUserOutput(name=name, email=email, role=role)


# ─────────────────────────────────────────────────────────────────────
# 2. Tool that writes — uses ToolContext for run-scoped paths.
# ─────────────────────────────────────────────────────────────────────

class ReportInput(BaseModel):
    subject: str
    body: str

class ReportOutput(BaseModel):
    path: str
    bytes_written: int


@tool
async def write_report(args: ReportInput, ctx: ToolContext) -> ReportOutput:
    """Persist a markdown report under the run's outputs/ directory.

    Use when the user asks you to "save", "write", or "produce" a report.
    The file lands in <run>/agent_cwd/outputs/ and surfaces as
    RunResult.output_files when the run finishes.
    """
    ctx.outputs_dir.mkdir(parents=True, exist_ok=True)
    out_path = ctx.outputs_dir / f"report_{args.subject}.md"
    out_path.write_text(args.body, encoding="utf-8")
    return ReportOutput(path=str(out_path), bytes_written=len(args.body.encode("utf-8")))


# ─────────────────────────────────────────────────────────────────────
# 3. Sync function — auto-wrapped in asyncio.to_thread.
# ─────────────────────────────────────────────────────────────────────

class HashInput(BaseModel):
    data: str

class HashOutput(BaseModel):
    sha256: str


@tool
def hash_payload(args: HashInput) -> HashOutput:
    """Compute the SHA-256 hex digest of arbitrary string data."""
    return HashOutput(sha256=hashlib.sha256(args.data.encode("utf-8")).hexdigest())


# ─────────────────────────────────────────────────────────────────────
# Wire them up + run
# ─────────────────────────────────────────────────────────────────────

PROMPT = (
    "Do all three steps in order:\n"
    "1) Fetch user 42, then state their role in one sentence.\n"
    "2) Hash the string 'sophia-motor' and report the digest.\n"
    "3) Save a short report under the subject 'demo' summarizing what you did.\n"
    "Use the available tools — do not make up data."
)


async def main() -> None:
    motor = Motor(MotorConfig(
        # Heterogeneous list: built-in name strings + @tool callables.
        # The model sees them as `mcp__sophia__fetch_user`, etc.
        default_tools=[fetch_user, write_report, hash_payload],
        default_max_turns=8,
        # Audit dump on so you can `cat <run>/audit/tool_*.json` after.
        proxy_dump_payloads=True,
        console_log_enabled=True,
    ))

    # Subscribe to python_tool_call events so we see hits scroll by live.
    @motor.on_event
    def watch(ev):
        if ev.type == "python_tool_call":
            ok = "✓" if ev.payload.get("ok") else "✗"
            print(
                f"  [tool] {ok} {ev.payload['name']} "
                f"({ev.payload['duration_ms']}ms)"
            )

    result = await motor.run(RunTask(prompt=PROMPT))

    print()
    print("=" * 60)
    print(f"Run ID:    {result.run_id}")
    print(f"Workspace: {result.workspace_dir}")
    print(f"Audit:     {result.audit_dir}")
    print(f"Tools called: {result.metadata.n_tool_calls}")
    print(f"Cost:      ${result.metadata.total_cost_usd:.4f}")
    print()
    print("--- assistant text ---")
    print(result.output_text or "(no text)")
    print()
    if result.output_files:
        print(f"--- output files ({len(result.output_files)}) ---")
        for f in result.output_files:
            print(f"  {f.relative_path}  ({f.size}B, {f.mime})")

    # Show the per-tool audit dumps (one file per call, sequenced).
    audit = Path(result.audit_dir)
    tool_dumps = sorted(audit.glob("tool_*.json"))
    if tool_dumps:
        print()
        print(f"--- tool audit dumps ({len(tool_dumps)}) ---")
        for f in tool_dumps:
            payload = json.loads(f.read_text())
            print(
                f"  {f.name}  →  input={payload['input']}  "
                f"output={payload['output']}  "
                f"({payload['duration_ms']}ms)"
            )


if __name__ == "__main__":
    asyncio.run(main())
