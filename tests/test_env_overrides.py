# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Cascade: explicit MotorConfig param > SOPHIA_MOTOR_* env > hardcoded default.

Each test runs in its own tmp_path with no `.env` present, so the
resolution cascade only sees process env vars or hardcoded defaults.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sophia_motor import MotorConfig


_ENV_KEYS = (
    "SOPHIA_MOTOR_WORKSPACE_ROOT",
    "SOPHIA_MOTOR_MODEL",
    "SOPHIA_MOTOR_PROXY_HOST",
    "SOPHIA_MOTOR_CONSOLE_LOG",
    "SOPHIA_MOTOR_AUDIT_DUMP",
    "SOPHIA_MOTOR_PERSIST_RUN_METADATA",
    "SOPHIA_MOTOR_BASE_URL",
    "SOPHIA_MOTOR_ADAPTER",
)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Strip every SOPHIA_MOTOR_* var and run from an empty cwd."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_defaults_when_no_env_no_dotenv(clean_env) -> None:
    c = MotorConfig(api_key="x")
    assert c.model == "claude-opus-4-6"
    assert c.proxy_host == "127.0.0.1"
    expected_default = (Path(tempfile.gettempdir()) / "sophia-motor" / "runs").resolve()
    assert c.workspace_root == expected_default
    assert c.console_log_enabled is False
    assert c.proxy_dump_payloads is False
    assert c.persist_run_metadata is False
    assert c.upstream_base_url == "https://api.anthropic.com"
    assert c.upstream_adapter == "anthropic"


def test_default_workspace_lives_in_tempdir(clean_env) -> None:
    """Default is ephemeral — fire-and-forget by design. Persistence is
    opt-in via `workspace_root=...` or `SOPHIA_MOTOR_WORKSPACE_ROOT`."""
    c = MotorConfig(api_key="x")
    tmp = Path(tempfile.gettempdir()).resolve()
    assert c.workspace_root.is_relative_to(tmp), (
        f"default workspace_root {c.workspace_root} must live under "
        f"tempfile.gettempdir() ({tmp})"
    )


def test_env_overrides_defaults(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SOPHIA_MOTOR_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SOPHIA_MOTOR_PROXY_HOST", "0.0.0.0")
    monkeypatch.setenv("SOPHIA_MOTOR_WORKSPACE_ROOT", str(clean_env / "runs"))
    monkeypatch.setenv("SOPHIA_MOTOR_CONSOLE_LOG", "true")
    monkeypatch.setenv("SOPHIA_MOTOR_AUDIT_DUMP", "yes")
    monkeypatch.setenv("SOPHIA_MOTOR_PERSIST_RUN_METADATA", "1")

    c = MotorConfig(api_key="x")
    assert c.model == "claude-haiku-4-5"
    assert c.proxy_host == "0.0.0.0"
    assert c.workspace_root == (clean_env / "runs").resolve()
    assert c.console_log_enabled is True
    assert c.proxy_dump_payloads is True
    assert c.persist_run_metadata is True


def test_explicit_param_beats_env(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SOPHIA_MOTOR_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("SOPHIA_MOTOR_CONSOLE_LOG", "true")
    monkeypatch.setenv("SOPHIA_MOTOR_AUDIT_DUMP", "true")
    monkeypatch.setenv("SOPHIA_MOTOR_PERSIST_RUN_METADATA", "true")

    c = MotorConfig(
        api_key="x",
        model="claude-opus-4-6",
        console_log_enabled=False,
        proxy_dump_payloads=False,
        persist_run_metadata=False,
    )
    assert c.model == "claude-opus-4-6"
    assert c.console_log_enabled is False
    assert c.proxy_dump_payloads is False
    assert c.persist_run_metadata is False


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True), ("on", True),
    ("True", True), ("YES", True), (" on ", True),
    ("false", False), ("0", False), ("no", False), ("off", False),
    ("False", False), ("NO", False),
])
def test_bool_parser_accepts_common_truthy_falsy(clean_env, monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("SOPHIA_MOTOR_CONSOLE_LOG", raw)
    c = MotorConfig(api_key="x")
    assert c.console_log_enabled is expected


def test_bool_parser_falls_back_on_garbage(clean_env, monkeypatch) -> None:
    """Typo'd values fall back on the hardcoded default — no silent coercion."""
    monkeypatch.setenv("SOPHIA_MOTOR_CONSOLE_LOG", "tru")
    monkeypatch.setenv("SOPHIA_MOTOR_AUDIT_DUMP", "maybe")
    c = MotorConfig(api_key="x")
    assert c.console_log_enabled is False
    assert c.proxy_dump_payloads is False


def test_workspace_root_expands_user(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SOPHIA_MOTOR_WORKSPACE_ROOT", "~/scratch/motor")
    c = MotorConfig(api_key="x")
    assert c.workspace_root == (Path.home() / "scratch" / "motor").resolve()


def test_dotenv_file_picked_up_when_no_process_env(clean_env) -> None:
    (clean_env / ".env").write_text(
        "SOPHIA_MOTOR_MODEL=claude-from-dotenv\n"
        "SOPHIA_MOTOR_AUDIT_DUMP=true\n"
    )
    c = MotorConfig(api_key="x")
    assert c.model == "claude-from-dotenv"
    assert c.proxy_dump_payloads is True


def test_process_env_beats_dotenv_file(clean_env, monkeypatch) -> None:
    (clean_env / ".env").write_text("SOPHIA_MOTOR_MODEL=from-dotenv\n")
    monkeypatch.setenv("SOPHIA_MOTOR_MODEL", "from-process-env")
    c = MotorConfig(api_key="x")
    assert c.model == "from-process-env"


def test_upstream_env_overrides_defaults(clean_env, monkeypatch) -> None:
    """SOPHIA_MOTOR_BASE_URL + SOPHIA_MOTOR_ADAPTER swap upstream from env alone."""
    monkeypatch.setenv("SOPHIA_MOTOR_BASE_URL", "http://localhost:8001")
    monkeypatch.setenv("SOPHIA_MOTOR_ADAPTER", "vllm")

    c = MotorConfig(api_key="x")
    assert c.upstream_base_url == "http://localhost:8001"
    assert c.upstream_adapter == "vllm"


def test_upstream_explicit_param_beats_env(clean_env, monkeypatch) -> None:
    monkeypatch.setenv("SOPHIA_MOTOR_BASE_URL", "http://localhost:8001")
    monkeypatch.setenv("SOPHIA_MOTOR_ADAPTER", "vllm")

    c = MotorConfig(
        api_key="x",
        upstream_base_url="https://api.anthropic.com",
        upstream_adapter="anthropic",
    )
    assert c.upstream_base_url == "https://api.anthropic.com"
    assert c.upstream_adapter == "anthropic"


def test_upstream_dotenv_picked_up(clean_env) -> None:
    (clean_env / ".env").write_text(
        "SOPHIA_MOTOR_BASE_URL=http://vllm.internal:8001\n"
        "SOPHIA_MOTOR_ADAPTER=vllm\n"
    )
    c = MotorConfig(api_key="x")
    assert c.upstream_base_url == "http://vllm.internal:8001"
    assert c.upstream_adapter == "vllm"
