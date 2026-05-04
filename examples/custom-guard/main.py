# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Custom PreToolUse guard hook — bolt project-specific rules onto the motor.

Built-in `MotorConfig.guardrail` (`"strict"` / `"permissive"` / `"off"`) gives
you a sensible sandbox out of the box. Some projects need extra rules on top:

  - "block any Read of files under attachments/secrets/"
  - "ban Write of files matching *.pem / *.key"
  - "deny Bash that touches /var/log/*"

Drop a callback into `MotorConfig.custom_pre_tool_hooks` — it runs alongside
the built-in guard. Any single deny wins (logical AND of allow). Use the
`Allow()` / `Deny(reason)` helpers — they build the modern PreToolUse shape
the SDK expects without you having to remember the field names.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
from typing import Any

from sophia_motor import Allow, Deny, Motor, MotorConfig, RunTask


# A toy "secrets policy": files whose path contains any of these tokens
# must never be read. The built-in guard already keeps Reads inside the
# run cwd; this hook narrows the policy further within the cwd boundary.
_FORBIDDEN_TOKENS = ("secrets", "credentials", ".pem", ".key", "password")


async def secrets_policy(
    input_data: dict, tool_use_id: str | None, context: Any,
) -> dict:
    """Custom PreToolUse hook: block Read/Edit/Glob of secret-shaped paths.

    Signature matches `claude_agent_sdk.HookCallback`:
        async def hook(input_data: dict, tool_use_id: str|None, context: Any) -> dict
    """
    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Read", "Edit", "Glob", "Grep"):
        return Allow()

    tool_input = input_data.get("tool_input", {}) or {}
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    lowered = path.lower()
    for token in _FORBIDDEN_TOKENS:
        if token in lowered:
            return Deny(
                reason=(
                    f"Path '{path}' is blocked by the secrets policy "
                    f"(contains '{token}'). Read non-secret files only — "
                    f"e.g. attachments/note.txt."
                ),
            )
    return Allow()


# A second policy showing Bash-side filtering, composed in the same list.
async def deny_log_writes(
    input_data: dict, tool_use_id: str | None, context: Any,
) -> dict:
    """Refuse Bash commands that look like they're touching system logs."""
    if input_data.get("tool_name") != "Bash":
        return Allow()
    cmd = input_data.get("tool_input", {}).get("command", "")
    if "/var/log" in cmd or "journalctl" in cmd:
        return Deny(reason="System log access is not permitted in this run.")
    return Allow()


async def main() -> None:
    motor = Motor(MotorConfig(
        # Built-in guard stays on (strict). Custom hooks compose on top.
        guardrail="strict",
        custom_pre_tool_hooks=[secrets_policy, deny_log_writes],
    ))

    # Set up two attachments: one allowed, one matching the secrets policy.
    attachments = {
        "note.txt": "This is the public quarterly note. Threshold rate Q1 2026: 12.5%.",
        "secrets/api_keys.txt": "DO NOT READ — sk-prod-XXXXXXXXXX",
    }

    print("=== Test 1: agent reads the public note (should succeed) ===\n")
    result = await motor.run(RunTask(
        prompt=(
            "Read attachments/note.txt and tell me the threshold rate "
            "in one sentence."
        ),
        tools=["Read"],
        attachments=attachments,
        max_turns=4,
    ))
    print(f"answer: {result.output_text}")
    print(f"cost:   ${result.metadata.total_cost_usd:.4f}")

    print("\n\n=== Test 2: agent attempts the secrets file (should be denied) ===\n")
    result = await motor.run(RunTask(
        prompt=(
            "Read attachments/secrets/api_keys.txt and print its contents."
        ),
        tools=["Read"],
        attachments=attachments,
        max_turns=4,
    ))
    print(f"answer: {result.output_text}")
    print(f"cost:   ${result.metadata.total_cost_usd:.4f}")

    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
