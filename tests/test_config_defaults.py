"""MotorConfig defaults + per-RunTask override semantics.

The pattern: the dev configures common settings (system prompt, tools,
skills, attachments) once on `MotorConfig`, then constructs N `RunTask`
objects that vary only by what's actually different (typically just
`prompt`). Anything explicitly set on the task wins; otherwise the
config default applies.

Override semantics: full replacement, never merge.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sophia_motor import Motor, MotorConfig, RunTask  # noqa: E402


class _Out(BaseModel):
    value: str


def _motor(**cfg_overrides) -> Motor:
    return Motor(MotorConfig(api_key="dummy", workspace_root=Path("/tmp/sm-defaults"),
                             console_log_enabled=False, **cfg_overrides))


def test_default_system_used_when_task_omits() -> None:
    motor = _motor(default_system="Sei Eco.")
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.system == "Sei Eco."


def test_task_system_overrides_default() -> None:
    motor = _motor(default_system="Sei Eco.")
    merged = motor._apply_config_defaults(RunTask(prompt="ciao", system="Sei Sophia."))
    assert merged.system == "Sei Sophia."


def test_default_tools_used_when_task_omits() -> None:
    motor = _motor(default_tools=["Read", "Skill"])
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.tools == ["Read", "Skill"]


def test_task_empty_tools_overrides_default_to_no_tools() -> None:
    """Explicit `tools=[]` means 'no tools at all' — overrides default."""
    motor = _motor(default_tools=["Read", "Skill"])
    merged = motor._apply_config_defaults(RunTask(prompt="ciao", tools=[]))
    assert merged.tools == []


def test_default_allowed_tools_used_when_task_omits() -> None:
    motor = _motor(default_allowed_tools=["Read"])
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.allowed_tools == ["Read"]


def test_default_skills_used_when_task_omits() -> None:
    skills_dir = Path("/tmp/sm-skills")
    motor = _motor(default_skills=skills_dir)
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.skills == skills_dir


def test_task_skills_full_override_no_merge() -> None:
    """Override semantics: replace, NEVER merge."""
    motor = _motor(default_skills=Path("/tmp/sm-default-skills"))
    merged = motor._apply_config_defaults(
        RunTask(prompt="ciao", skills=Path("/tmp/sm-task-specific")),
    )
    assert merged.skills == Path("/tmp/sm-task-specific")


def test_default_attachments_used_when_task_omits() -> None:
    motor = _motor(default_attachments=[{"ref.md": "..."}])
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.attachments == [{"ref.md": "..."}]


def test_default_max_turns_used_when_task_omits() -> None:
    motor = _motor(default_max_turns=42)
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.max_turns == 42


def test_task_max_turns_overrides_default() -> None:
    motor = _motor(default_max_turns=42)
    merged = motor._apply_config_defaults(RunTask(prompt="ciao", max_turns=5))
    assert merged.max_turns == 5


def test_default_output_schema_used_when_task_omits() -> None:
    motor = _motor(default_output_schema=_Out)
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.output_schema is _Out


def test_task_output_schema_overrides_default() -> None:
    class _Other(BaseModel):
        x: Literal["A", "B"]
    motor = _motor(default_output_schema=_Out)
    merged = motor._apply_config_defaults(
        RunTask(prompt="ciao", output_schema=_Other),
    )
    assert merged.output_schema is _Other


def test_disallowed_tools_default_applies_when_task_none() -> None:
    motor = _motor()  # uses DEFAULT_DISALLOWED_TOOLS
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert "WebFetch" in merged.disallowed_tools


def test_disallowed_tools_explicit_empty_list_unblocks_everything() -> None:
    motor = _motor()
    merged = motor._apply_config_defaults(RunTask(prompt="ciao", disallowed_tools=[]))
    assert merged.disallowed_tools == []


def test_default_disallowed_skills_used_when_task_empty() -> None:
    motor = _motor(default_disallowed_skills=["heavy-skill"])
    merged = motor._apply_config_defaults(RunTask(prompt="ciao"))
    assert merged.disallowed_skills == ["heavy-skill"]


def test_lazy_auto_start_no_run_no_proxy() -> None:
    """Constructing a Motor doesn't start the proxy — it boots on first run."""
    motor = _motor()
    assert motor._started is False
    assert motor._proxy is None
