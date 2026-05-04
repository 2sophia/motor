# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for `MotorConfig.cli_*` flags → extra_args wiring.

No API calls — exercise `Motor._build_sdk_options` and inspect the
resulting `ClaudeAgentOptions.extra_args` dict.
"""
from __future__ import annotations

from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


def _build(tmp_path: Path, **cfg_overrides) -> dict:
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        **cfg_overrides,
    ))
    opts = motor._build_sdk_options(
        RunTask(prompt="x"),
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
        run_id="run-test",
    )
    return dict(opts.extra_args or {})


def test_strict_mcp_config_on_by_default(tmp_path: Path) -> None:
    args = _build(tmp_path)
    assert "strict-mcp-config" in args
    assert args["strict-mcp-config"] is None  # boolean flag


def test_strict_mcp_config_can_be_disabled(tmp_path: Path) -> None:
    args = _build(tmp_path, cli_strict_mcp_config=False)
    assert "strict-mcp-config" not in args


def test_no_session_persistence_on_by_default(tmp_path: Path) -> None:
    args = _build(tmp_path)
    assert "no-session-persistence" in args
    assert args["no-session-persistence"] is None


def test_custom_pre_tool_hooks_composed_with_built_in(tmp_path: Path) -> None:
    """Custom hooks land in the same PreToolUse HookMatcher as the
    built-in guard — order: built-in first, then user hooks."""
    async def my_hook(input_data, tool_use_id, context):
        return {}

    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        custom_pre_tool_hooks=[my_hook],
    ))
    opts = motor._build_sdk_options(
        RunTask(prompt="x"),
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
        run_id="run-test",
    )
    pretool = opts.hooks["PreToolUse"]
    assert len(pretool) == 1  # one HookMatcher
    matcher_hooks = pretool[0].hooks
    # built-in guard (strict default) + my_hook
    assert len(matcher_hooks) == 2
    assert matcher_hooks[1] is my_hook  # user hook is last (after built-in)


def test_custom_pre_tool_hooks_alone_when_guardrail_off(tmp_path: Path) -> None:
    """With `guardrail='off'` only the user hooks run — the built-in is
    fully removed, no zombie matcher with an empty hooks list."""
    async def my_hook(input_data, tool_use_id, context):
        return {}

    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        guardrail="off",
        custom_pre_tool_hooks=[my_hook],
    ))
    opts = motor._build_sdk_options(
        RunTask(prompt="x"),
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
        run_id="run-test",
    )
    pretool = opts.hooks["PreToolUse"]
    matcher_hooks = pretool[0].hooks
    assert matcher_hooks == [my_hook]


def test_no_hooks_block_when_off_and_no_custom(tmp_path: Path) -> None:
    """`guardrail='off'` + no custom hooks → no PreToolUse block at all."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        guardrail="off",
    ))
    opts = motor._build_sdk_options(
        RunTask(prompt="x"),
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
        run_id="run-test",
    )
    assert not opts.hooks or "PreToolUse" not in opts.hooks


def test_chat_mode_skips_strict_mcp_config(tmp_path: Path) -> None:
    """chat-mode runs reuse caller-managed workspace; ambient MCP discovery
    is the caller's responsibility, so the motor doesn't override it."""
    motor = Motor(MotorConfig(
        api_key="dummy", workspace_root=tmp_path, console_log_enabled=False,
    ))
    chat_root = tmp_path / "chat-root"
    chat_root.mkdir()
    task = RunTask(prompt="x", workspace_dir=chat_root)
    opts = motor._build_sdk_options(
        task,
        agent_cwd=chat_root / "cwd",
        claude_dir=chat_root / ".claude",
        api_key="dummy",
        run_id="run-test",
    )
    args = dict(opts.extra_args or {})
    assert "strict-mcp-config" not in args
    assert "no-session-persistence" not in args
