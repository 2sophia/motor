"""Workspace layout — deterministic structure tests, no API calls.

Verifies the sophia-agent pattern: CLAUDE_CONFIG_DIR is a SIBLING of the
SDK cwd, never a descendant. When `.claude/` lives inside the cwd, the
CLI mis-resolves session paths and recreates the workspace structure
inside cwd as `./.runs/<RID>/agent_cwd/.claude/` — verified empirically.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sophia_motor import Motor, MotorConfig  # noqa: E402


def _setup(tmp_path: Path, *, skills_root: Path | None = None):
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))
    skills = [skills_root] if skills_root else []
    return motor._setup_workspace("run-layout-test", [], skills, [])


def test_claude_dir_is_sibling_of_agent_cwd(tmp_path: Path) -> None:
    workspace, agent_cwd, audit, claude_dir, _, _ = _setup(tmp_path)
    assert claude_dir.parent == workspace
    assert agent_cwd.parent == workspace
    assert claude_dir != agent_cwd
    cwd_str = str(agent_cwd) + "/"
    assert not str(claude_dir).startswith(cwd_str), (
        f"claude_dir {claude_dir} must NOT be inside agent_cwd {agent_cwd}"
    )


def test_run_root_contains_only_motor_owned_entries(tmp_path: Path) -> None:
    workspace, agent_cwd, audit, claude_dir, _, _ = _setup(tmp_path)
    entries = {p.name for p in workspace.iterdir()}
    assert entries == {"audit", ".claude", "agent_cwd"}


def test_agent_cwd_holds_only_attachments_and_outputs(tmp_path: Path) -> None:
    workspace, agent_cwd, _, _, _, _ = _setup(tmp_path)
    entries = {p.name for p in agent_cwd.iterdir()}
    assert entries == {"attachments", "outputs"}


def test_claude_dir_has_skills_subdir(tmp_path: Path) -> None:
    _, _, _, claude_dir, _, _ = _setup(tmp_path)
    assert (claude_dir / "skills").is_dir()


def test_skills_are_symlinked_into_claude_dir(tmp_path: Path) -> None:
    skills_src = Path(__file__).parent.parent / "examples" / "skills_example"
    _, _, _, claude_dir, _, manifest = _setup(tmp_path, skills_root=skills_src)
    say_hello = claude_dir / "skills" / "say_hello"
    assert say_hello.is_symlink()
    assert say_hello.resolve() == (skills_src / "say_hello").resolve()
    assert "say_hello" in manifest


def test_audit_dir_under_run_root_not_agent_cwd(tmp_path: Path) -> None:
    workspace, agent_cwd, audit, _, _, _ = _setup(tmp_path)
    assert audit.parent == workspace
    cwd_str = str(agent_cwd) + "/"
    assert not str(audit).startswith(cwd_str)
