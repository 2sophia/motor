# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Subagent pass-through + opt-in validation.

By design, MotorConfig.default_disallowed_tools includes "Agent". The dev who
wants subagents must opt in by:
  1. adding "Agent" to RunTask.tools (or MotorConfig.default_tools)
  2. removing "Agent" from RunTask.disallowed_tools (or overriding the default)
  3. passing RunTask.agents={...} (or MotorConfig.default_agents={...})

Anything missing → RuntimeError at run time with clear instructions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sophia_motor import (
    AgentDefinition,
    Motor,
    MotorConfig,
    RunTask,
)


def _agent_def() -> AgentDefinition:
    return AgentDefinition(
        description="Test reviewer.",
        prompt="You are a test reviewer.",
        tools=["Read", "Grep"],
    )


def test_agent_definition_re_export() -> None:
    """sophia_motor re-exports AgentDefinition from claude_agent_sdk."""
    a = AgentDefinition(description="x", prompt="y")
    assert a.description == "x"
    assert a.prompt == "y"


async def test_default_agents_field_defaults_to_empty(tmp_path: Path) -> None:
    cfg = MotorConfig(api_key="x", workspace_root=tmp_path, console_log_enabled=False)
    assert cfg.default_agents == {}


async def test_explicit_setup_passes_through_to_sdk(tmp_path: Path) -> None:
    """tools=['Agent'] + disallowed_tools=[] + agents={'x': ...} → forwarded."""
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False))
    task = motor._apply_config_defaults(RunTask(
        prompt="x",
        tools=["Read", "Agent"],
        disallowed_tools=[],
        agents={"reviewer": _agent_def()},
    ))
    opts = motor._build_sdk_options(
        task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
        api_key="dummy", run_id="r1",
    )
    assert "reviewer" in opts.agents
    assert opts.tools == ["Read", "Agent"]
    assert "Agent" not in opts.disallowed_tools


async def test_default_agents_applied_when_task_omits(tmp_path: Path) -> None:
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        default_agents={"shared": _agent_def()},
        default_tools=["Read", "Agent"],
        default_disallowed_tools=[],  # explicit override removes Agent block
    ))
    task = motor._apply_config_defaults(RunTask(prompt="x"))
    assert "shared" in task.agents
    opts = motor._build_sdk_options(
        task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
        api_key="dummy", run_id="r1",
    )
    assert "shared" in opts.agents


async def test_task_agents_override_default(tmp_path: Path) -> None:
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        default_agents={"shared": _agent_def()},
        default_tools=["Read", "Agent"],
        default_disallowed_tools=[],
    ))
    task = motor._apply_config_defaults(RunTask(
        prompt="x",
        agents={"task-only": _agent_def()},
    ))
    assert list(task.agents) == ["task-only"]


async def test_task_agents_empty_dict_disables_subagents(tmp_path: Path) -> None:
    """Explicit `agents={}` is a real override → no subagents this run."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
        default_agents={"shared": _agent_def()},
        default_tools=["Read", "Agent"],
        default_disallowed_tools=[],
    ))
    task = motor._apply_config_defaults(RunTask(
        prompt="x",
        agents={},
    ))
    assert task.agents == {}
    opts = motor._build_sdk_options(
        task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
        api_key="dummy", run_id="r1",
    )
    # When agents is empty, we don't forward `agents` at all.
    assert getattr(opts, "agents", None) in (None, {})


async def test_agents_without_agent_in_tools_raises(tmp_path: Path) -> None:
    """agents={'x': ...} but tools=['Read'] (no Agent) → RuntimeError."""
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False))
    task = motor._apply_config_defaults(RunTask(
        prompt="x",
        tools=["Read"],
        disallowed_tools=[],
        agents={"x": _agent_def()},
    ))
    with pytest.raises(RuntimeError, match="add 'Agent' to RunTask.tools"):
        motor._build_sdk_options(
            task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
            api_key="dummy", run_id="r1",
        )


async def test_agents_with_agent_in_disallowed_raises(tmp_path: Path) -> None:
    """Tools=None (no whitelist) + agents set + default disallowed includes
    Agent → the conflict-resolution shortcut doesn't fire (it only kicks in
    when task.tools is an explicit list) → Agent stays blocked → RuntimeError.
    """
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False,
                              default_tools=None))  # SDK's claude_code preset
    task = motor._apply_config_defaults(RunTask(
        prompt="x",
        # tools left as None → no explicit whitelist, no conflict-resolution
        agents={"x": _agent_def()},
    ))
    with pytest.raises(RuntimeError, match="remove 'Agent' from RunTask.disallowed_tools"):
        motor._build_sdk_options(
            task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
            api_key="dummy", run_id="r1",
        )


async def test_no_agents_no_validation(tmp_path: Path) -> None:
    """Without agents, no Agent-tool validation kicks in. Backwards compatible."""
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False))
    task = motor._apply_config_defaults(RunTask(prompt="x", tools=["Read"]))
    # Should not raise — Agent is not requested.
    opts = motor._build_sdk_options(
        task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
        api_key="dummy", run_id="r1",
    )
    assert opts is not None


async def test_agent_built_in_general_purpose_pattern(tmp_path: Path) -> None:
    """Edge case: tools=['Agent'] + agents=None → general-purpose subagent
    pattern. The motor must NOT raise here — agents is empty/None means
    no custom subagents declared, and the SDK exposes the built-in
    general-purpose agent through the Agent tool alone."""
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False))
    task = motor._apply_config_defaults(RunTask(
        prompt="x",
        tools=["Read", "Agent"],
        disallowed_tools=[],
        # agents intentionally omitted (None → falls back to {} default)
    ))
    opts = motor._build_sdk_options(
        task=task, agent_cwd=tmp_path, claude_dir=tmp_path,
        api_key="dummy", run_id="r1",
    )
    # No agents param forwarded (empty), but Agent is in tools.
    assert "Agent" in opts.tools
