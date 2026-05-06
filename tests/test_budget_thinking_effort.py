# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for `MotorConfig.default_max_budget_usd`,
`default_thinking`, `default_effort` and the matching `RunTask`
fields → `ClaudeAgentOptions` wiring.

No API calls — exercise `Motor._build_sdk_options` and inspect the
resulting `ClaudeAgentOptions`.
"""
from __future__ import annotations

from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


def _opts(tmp_path: Path, task: RunTask, **cfg_overrides):
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        **cfg_overrides,
    ))
    return motor._build_sdk_options(
        task,
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
        run_id="run-test",
    )


def test_defaults_are_none(tmp_path: Path) -> None:
    """Out of the box, MotorConfig and RunTask leave the three knobs unset."""
    cfg = MotorConfig(api_key="dummy", workspace_root=tmp_path)
    assert cfg.default_max_budget_usd is None
    assert cfg.default_thinking is None
    assert cfg.default_effort is None
    task = RunTask(prompt="x")
    assert task.max_budget_usd is None
    assert task.thinking is None
    assert task.effort is None


def test_no_kwarg_when_both_unset(tmp_path: Path) -> None:
    """Nothing set on task or config → SDK sees no effort/thinking/budget kwarg.
    The CLI / model uses its own defaults; we don't shadow them."""
    opts = _opts(tmp_path, RunTask(prompt="x"))
    assert opts.effort is None
    assert opts.thinking is None
    assert opts.max_budget_usd is None


def test_task_fields_passed_to_sdk(tmp_path: Path) -> None:
    """All three task fields land on ClaudeAgentOptions verbatim."""
    opts = _opts(tmp_path, RunTask(
        prompt="x",
        effort="low",
        thinking={"type": "adaptive"},
        max_budget_usd=2.5,
    ))
    assert opts.effort == "low"
    assert opts.thinking == {"type": "adaptive"}
    assert opts.max_budget_usd == 2.5


def test_config_defaults_inherited_when_task_none(tmp_path: Path) -> None:
    """task.X = None → config.default_X is used."""
    opts = _opts(
        tmp_path,
        RunTask(prompt="x"),
        default_effort="medium",
        default_thinking={"type": "enabled", "budget_tokens": 1024},
        default_max_budget_usd=10.0,
    )
    assert opts.effort == "medium"
    assert opts.thinking == {"type": "enabled", "budget_tokens": 1024}
    assert opts.max_budget_usd == 10.0


def test_task_wins_over_config_default(tmp_path: Path) -> None:
    """When both task and config set the same knob, task wins (full replace,
    same convention as every other RunTask field)."""
    opts = _opts(
        tmp_path,
        RunTask(
            prompt="x",
            effort="high",
            thinking={"type": "disabled"},
            max_budget_usd=0.5,
        ),
        default_effort="low",
        default_thinking={"type": "adaptive"},
        default_max_budget_usd=99.0,
    )
    assert opts.effort == "high"
    assert opts.thinking == {"type": "disabled"}
    assert opts.max_budget_usd == 0.5


def test_three_fields_are_independent(tmp_path: Path) -> None:
    """Setting only one of the three doesn't pull the others along."""
    opts = _opts(tmp_path, RunTask(prompt="x", max_budget_usd=1.0))
    assert opts.max_budget_usd == 1.0
    assert opts.effort is None
    assert opts.thinking is None


def test_thinking_disabled_shape_passes_through(tmp_path: Path) -> None:
    """The `{'type': 'disabled'}` shape is forwarded as-is — even though it's
    falsy-looking, it's a non-empty dict so the if-guard accepts it."""
    opts = _opts(tmp_path, RunTask(prompt="x", thinking={"type": "disabled"}))
    assert opts.thinking == {"type": "disabled"}
