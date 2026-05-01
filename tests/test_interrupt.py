# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for motor.interrupt() — no API key required.

The interesting live coverage (interrupt actually aborts an in-flight
run) is exercised manually in `examples/interrupt/main.py`; verifying
it in pytest would require mocking `ClaudeSDKClient.receive_response()`
which couples tests to SDK internals.
"""
from __future__ import annotations

import asyncio

from sophia_motor import Motor, MotorConfig


async def test_interrupt_returns_false_when_no_run_active(tmp_path):
    """`interrupt()` is idempotent — never raises, returns False if there's
    nothing to interrupt."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))
    assert await motor.interrupt() is False
    assert await motor.interrupt(run_id="run-anything") is False


async def test_interrupt_returns_false_for_mismatched_run_id(tmp_path):
    """When `run_id` is given, only the active run that matches is
    interrupted. Mismatch → False (race-condition guard)."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    class _FakeClient:
        async def interrupt(self):
            raise AssertionError("should not be called for mismatched run_id")

    motor._current_client = _FakeClient()  # type: ignore[assignment]
    motor._current_run_id = "run-current"

    assert await motor.interrupt(run_id="run-other") is False
    # And the registry is intact
    assert motor._current_run_id == "run-current"

    motor._current_client = None
    motor._current_run_id = None


async def test_interrupt_signals_client_when_run_id_matches(tmp_path):
    """Match → client.interrupt() is awaited and the method returns True."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    called = asyncio.Event()

    class _FakeClient:
        async def interrupt(self):
            called.set()

    motor._current_client = _FakeClient()  # type: ignore[assignment]
    motor._current_run_id = "run-current"

    result = await motor.interrupt(run_id="run-current")
    assert result is True
    assert called.is_set()
    assert motor._interrupt_requested is True

    motor._current_client = None
    motor._current_run_id = None
    motor._interrupt_requested = False


async def test_interrupt_signals_client_when_run_id_omitted(tmp_path):
    """`run_id=None` (default) means "interrupt whatever is current"."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    called = asyncio.Event()

    class _FakeClient:
        async def interrupt(self):
            called.set()

    motor._current_client = _FakeClient()  # type: ignore[assignment]
    motor._current_run_id = "run-active"

    assert await motor.interrupt() is True
    assert called.is_set()


async def test_interrupt_swallows_client_exception(tmp_path):
    """If `client.interrupt()` raises, we log + return True. The user
    asked to interrupt; they shouldn't have to worry about transient
    errors during the signal itself."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    class _FakeClient:
        async def interrupt(self):
            raise RuntimeError("transport gone")

    motor._current_client = _FakeClient()  # type: ignore[assignment]
    motor._current_run_id = "run-current"

    assert await motor.interrupt() is True
    assert motor._interrupt_requested is True
