# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Python tools across parent + subagents — two patterns shown together.

Pattern A — INHERITANCE (subagent's `tools=None`)
    Put your @tool callables in MotorConfig.default_tools (or RunTask.tools)
    and declare the AgentDefinition WITHOUT touching `tools` / `mcpServers`.
    The subagent inherits everything from the parent — SDK does the work,
    we ride along.

Pattern B — EXPLICIT RESTRICT (subagent's `tools=[callable, ...]`)
    Pass @tool callables directly inside `AgentDefinition.tools`. The motor
    collects every callable referenced anywhere in the run, mounts ONE
    MCP server with the union, and rewrites each subagent's `tools` list
    to the prefixed form (`mcp__sophia__<name>`) before forwarding to the
    SDK. Use this when you want to LIMIT what a specific subagent can do.

This example exercises both: `lookup` inherits the parent toolset, while
`writer` is explicitly scoped down to a single tool.
"""
from __future__ import annotations

import asyncio
import hashlib

from claude_agent_sdk import AgentDefinition
from pydantic import BaseModel

from sophia_motor import (
    Motor,
    MotorConfig,
    RunTask,
    ToolContext,
    tool,
)


# ─────────────────────────────────────────────────────────────────────
# Same toy tools as main.py — copy-pasted for self-containment.
# ─────────────────────────────────────────────────────────────────────

class FetchUserInput(BaseModel):
    user_id: int

class FetchUserOutput(BaseModel):
    name: str
    email: str
    role: str


_USERS = {
    1:  ("Alex",     "alex@example.com",  "ceo"),
    2:  ("Eco",      "eco@example.com",   "engineer"),
    42: ("Trillian", "tril@example.com",  "operator"),
}


@tool
async def fetch_user(args: FetchUserInput) -> FetchUserOutput:
    """Fetch a user record from the internal directory by integer ID."""
    rec = _USERS.get(args.user_id)
    if rec is None:
        raise KeyError(f"user_id {args.user_id} not found")
    name, email, role = rec
    return FetchUserOutput(name=name, email=email, role=role)


class HashInput(BaseModel):
    data: str

class HashOutput(BaseModel):
    sha256: str


@tool
def hash_payload(args: HashInput) -> HashOutput:
    """Compute the SHA-256 hex digest of arbitrary string data."""
    return HashOutput(sha256=hashlib.sha256(args.data.encode("utf-8")).hexdigest())


# ─────────────────────────────────────────────────────────────────────
# Tool used ONLY by the writer subagent — not on the parent.
# Demonstrates that a callable can live exclusively in AgentDefinition.tools
# and the motor mounts it on the shared MCP server anyway.
# ─────────────────────────────────────────────────────────────────────

class ReportInput(BaseModel):
    subject: str
    body: str

class ReportOutput(BaseModel):
    path: str
    bytes_written: int


@tool
async def write_report(args: ReportInput, ctx: ToolContext) -> ReportOutput:
    """Persist a markdown report to the run's outputs/ directory."""
    ctx.outputs_dir.mkdir(parents=True, exist_ok=True)
    out_path = ctx.outputs_dir / f"report_{args.subject}.md"
    out_path.write_text(args.body, encoding="utf-8")
    return ReportOutput(path=str(out_path), bytes_written=len(args.body.encode("utf-8")))


# ─────────────────────────────────────────────────────────────────────
# Subagents — two patterns side by side
# ─────────────────────────────────────────────────────────────────────

# Pattern A: tools=None → subagent inherits the parent's full toolset.
LOOKUP = AgentDefinition(
    description=(
        "Data-lookup specialist. Use for any task that needs internal "
        "user records or cryptographic digests."
    ),
    prompt=(
        "You are a data-lookup specialist. Use the available tools to "
        "answer the parent agent's questions concisely."
    ),
)

# Pattern B: tools=[write_report] → subagent restricted to ONLY this tool.
# Note: write_report is NOT in the parent's default_tools — it lives only
# here. The motor collects it into the shared MCP server regardless.
WRITER = AgentDefinition(
    description=(
        "Report writer. Use ONLY when the parent agent has prepared the "
        "exact text to persist and wants it saved as a markdown file."
    ),
    prompt=(
        "You are a precise report writer. Take the body and subject "
        "provided by the parent and persist them. Reply with a short "
        "confirmation including the saved path."
    ),
    tools=[write_report],
)


PROMPT = (
    "Run TWO subagent dispatches in order:\n"
    "  1) Use the 'lookup' subagent to fetch user 42 and hash the string "
    "'sophia-motor'. Bring back both results.\n"
    "  2) Use the 'writer' subagent to save a one-paragraph report under "
    "subject 'demo' that summarises step 1.\n"
    "Then reply with the saved path and a one-line summary."
)


async def main() -> None:
    motor = Motor(MotorConfig(
        # Parent has fetch_user + hash_payload + Agent (to spawn subs).
        # write_report is NOT here — it lives only inside WRITER.tools.
        default_tools=[fetch_user, hash_payload, "Agent"],
        default_agents={
            "lookup": LOOKUP,    # Pattern A — inheritance
            "writer": WRITER,    # Pattern B — explicit restrict
        },
        default_max_turns=10,
        proxy_dump_payloads=True,
        console_log_enabled=True,
    ))

    @motor.on_event
    def watch(ev):
        if ev.type == "python_tool_call":
            ok = "✓" if ev.payload.get("ok") else "✗"
            print(
                f"  [tool] {ok} {ev.payload['name']} "
                f"({ev.payload['duration_ms']}ms)"
            )
        elif ev.type == "tool_use" and ev.payload.get("tool") == "Agent":
            print(
                f"  [Agent] spawning subagent: "
                f"{ev.payload.get('input_keys', [])}"
            )

    result = await motor.run(RunTask(prompt=PROMPT))

    print()
    print("=" * 60)
    print(f"Run ID:    {result.run_id}")
    print(f"Tools called: {result.metadata.n_tool_calls}")
    print(f"Cost:      ${result.metadata.total_cost_usd:.4f}")
    print(f"Error:     {result.metadata.is_error}  reason={result.metadata.error_reason!r}")
    print()
    print("--- assistant text ---")
    print(result.output_text or "(no text)")


if __name__ == "__main__":
    asyncio.run(main())
