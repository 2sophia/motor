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

import pytest

from sophia_motor import Motor, MotorConfig
from sophia_motor.motor import _ActiveRun


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


async def test_interrupt_returns_false_for_unknown_run_id(tmp_path):
    """When `run_id` is given, only the active run that matches is
    interrupted. Unknown id → False (race-condition guard)."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    class _FakeClient:
        async def interrupt(self):
            raise AssertionError("should not be called for mismatched run_id")

    motor._active_runs["run-current"] = _ActiveRun(client=_FakeClient())  # type: ignore[arg-type]

    assert await motor.interrupt(run_id="run-other") is False
    # And the registry is intact
    assert "run-current" in motor._active_runs

    motor._active_runs.clear()


async def test_interrupt_signals_client_when_run_id_matches(tmp_path):
    """Match → client.interrupt() is awaited, interrupt_requested set, True."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    called = asyncio.Event()

    class _FakeClient:
        async def interrupt(self):
            called.set()

    motor._active_runs["run-current"] = _ActiveRun(client=_FakeClient())  # type: ignore[arg-type]

    result = await motor.interrupt(run_id="run-current")
    assert result is True
    assert called.is_set()
    assert motor._active_runs["run-current"].interrupt_requested is True

    motor._active_runs.clear()


async def test_interrupt_no_arg_when_single_run_targets_it(tmp_path):
    """`run_id=None` is unambiguous when exactly one run is active — that one."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    called = asyncio.Event()

    class _FakeClient:
        async def interrupt(self):
            called.set()

    motor._active_runs["run-only"] = _ActiveRun(client=_FakeClient())  # type: ignore[arg-type]

    assert await motor.interrupt() is True
    assert called.is_set()
    motor._active_runs.clear()


async def test_interrupt_no_arg_raises_when_multiple_runs(tmp_path):
    """`run_id=None` with >1 active runs is ambiguous — raise so the caller
    is forced to pick which one. Otherwise we'd silently interrupt
    'whichever happened to be first in the dict', which is a footgun."""
    motor = Motor(MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    ))

    class _FakeClient:
        async def interrupt(self):
            raise AssertionError("should not be called when ambiguous")

    motor._active_runs["run-a"] = _ActiveRun(client=_FakeClient())  # type: ignore[arg-type]
    motor._active_runs["run-b"] = _ActiveRun(client=_FakeClient())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="2 active runs"):
        await motor.interrupt()

    # Both runs intact, neither interrupted
    assert not motor._active_runs["run-a"].interrupt_requested
    assert not motor._active_runs["run-b"].interrupt_requested

    motor._active_runs.clear()


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

    motor._active_runs["run-current"] = _ActiveRun(client=_FakeClient())  # type: ignore[arg-type]

    assert await motor.interrupt() is True
    assert motor._active_runs["run-current"].interrupt_requested is True
    motor._active_runs.clear()
