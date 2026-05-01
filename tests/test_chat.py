# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for the Chat layer.

Live multi-turn coverage (the "does the model actually remember the
previous turn") lives in tests/run_smoke.py and the example —
exercising it in pytest would require a real ANTHROPIC_API_KEY which
we keep gated.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sophia_motor import Chat, Motor, MotorConfig, RunTask


def _make_motor(tmp_path: Path) -> Motor:
    return Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path / "runs",
        console_log_enabled=False,
    ))


def test_chat_factory_mints_id_when_omitted(tmp_path):
    chat = _make_motor(tmp_path).chat()
    assert chat.chat_id.startswith("chat-")
    assert chat.session_id is None
    assert chat.cwd.exists()


def test_chat_factory_accepts_explicit_id(tmp_path):
    chat = _make_motor(tmp_path).chat(chat_id="user-42-thread-9")
    assert chat.chat_id == "user-42-thread-9"
    assert chat.cwd.name == "user-42-thread-9"


def test_chat_factory_accepts_resume_session(tmp_path):
    chat = _make_motor(tmp_path).chat(
        chat_id="t1",
        session_id="dde29c2b-...-resumed",
    )
    assert chat.session_id == "dde29c2b-...-resumed"


def test_chat_direct_construction(tmp_path):
    motor = _make_motor(tmp_path)
    chat = Chat(motor, chat_id="direct-test")
    assert chat.chat_id == "direct-test"
    assert chat.motor is motor


def test_chat_root_isolates_per_chat_id(tmp_path):
    motor = _make_motor(tmp_path)
    a = motor.chat(chat_id="alpha")
    b = motor.chat(chat_id="beta")
    assert a.cwd != b.cwd
    assert a.cwd.name == "alpha"
    assert b.cwd.name == "beta"


def test_chat_root_lives_outside_runs_root(tmp_path):
    """Chat workspaces are siblings of runs/ — runs/ stays for standalone."""
    motor = _make_motor(tmp_path)
    chat = motor.chat(chat_id="x")
    # runs root: tmp_path/runs ; chat root: tmp_path/chats/x
    assert "chats" in chat.cwd.parts
    assert "runs" not in chat.cwd.parts
    assert motor.config.workspace_root.parent == chat.cwd.parent.parent


def test_chat_explicit_root_override(tmp_path):
    motor = _make_motor(tmp_path)
    custom = tmp_path / "custom-chats"
    chat = motor.chat(chat_id="x", root=custom)
    assert chat.cwd == custom / "x"


# ─── _build_task injection ──────────────────────────────────────────────

def test_build_task_from_string(tmp_path):
    chat = _make_motor(tmp_path).chat()
    task = chat._build_task("hello")
    assert isinstance(task, RunTask)
    assert task.prompt == "hello"
    assert task.workspace_dir == chat.cwd
    assert task.session_id is None  # first turn


def test_build_task_injects_session_id_after_set(tmp_path):
    chat = _make_motor(tmp_path).chat()
    chat.session_id = "abc-123"
    task = chat._build_task("continue")
    assert task.session_id == "abc-123"


def test_build_task_respects_explicit_session_id_in_runtask(tmp_path):
    """Power-user: pass a RunTask with an explicit session_id (e.g. for
    forking) — the chat's session_id does NOT override it."""
    chat = _make_motor(tmp_path).chat()
    chat.session_id = "chat-session"
    custom = RunTask(prompt="...", session_id="forked-session")
    task = chat._build_task(custom)
    assert task.session_id == "forked-session"


def test_build_task_respects_explicit_workspace_dir_in_runtask(tmp_path):
    chat = _make_motor(tmp_path).chat()
    custom_dir = tmp_path / "elsewhere"
    custom = RunTask(prompt="...", workspace_dir=custom_dir)
    task = chat._build_task(custom)
    assert task.workspace_dir == custom_dir


def test_build_task_rejects_bad_type(tmp_path):
    chat = _make_motor(tmp_path).chat()
    with pytest.raises(TypeError, match="expects str or RunTask"):
        chat._build_task(42)  # type: ignore[arg-type]


# ─── reset ──────────────────────────────────────────────────────────────

async def test_reset_clears_session_id(tmp_path):
    chat = _make_motor(tmp_path).chat()
    chat.session_id = "prior"
    await chat.reset()
    assert chat.session_id is None


async def test_reset_drops_session_files(tmp_path):
    chat = _make_motor(tmp_path).chat()
    fake_dir = chat.cwd / ".claude" / "projects" / "-encoded-cwd"
    fake_dir.mkdir(parents=True)
    (fake_dir / "old-session.jsonl").write_text('{"x": 1}')

    await chat.reset()
    assert not (chat.cwd / ".claude" / "projects").exists()


async def test_reset_keeps_chat_id_and_cwd(tmp_path):
    chat = _make_motor(tmp_path).chat(chat_id="keep-me")
    cwd_before = chat.cwd
    await chat.reset()
    assert chat.chat_id == "keep-me"
    assert chat.cwd == cwd_before
    assert chat.cwd.exists()
