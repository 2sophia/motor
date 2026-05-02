# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Proxy lifecycle: binding, port-in-use error, kernel-assigned free port."""
from __future__ import annotations

import socket

import pytest

from sophia_motor import Motor, MotorConfig


async def test_default_kernel_assigned_port_works(tmp_path) -> None:
    """proxy_port=None → kernel picks a free port, base_url is reachable."""
    config = MotorConfig(
        api_key="dummy",
        workspace_root=tmp_path,
        console_log_enabled=False,
    )
    async with Motor(config) as motor:
        assert motor._proxy is not None
        assert motor._proxy.port and motor._proxy.port > 0
        assert motor._proxy.base_url.startswith("http://127.0.0.1:")


async def test_explicit_busy_port_raises_clear_error(tmp_path) -> None:
    """proxy_port=N with N already bound → RuntimeError with a helpful hint."""
    # Park a socket on a free port so we know exactly which one is busy.
    squatter = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    squatter.bind(("127.0.0.1", 0))
    squatter.listen(1)
    busy_port = squatter.getsockname()[1]

    try:
        config = MotorConfig(
            api_key="dummy",
            workspace_root=tmp_path,
            console_log_enabled=False,
            proxy_port=busy_port,
        )
        with pytest.raises(RuntimeError, match="already in use"):
            async with Motor(config):
                pass  # should never get here
    finally:
        squatter.close()


async def test_two_motors_in_parallel_dont_collide(tmp_path) -> None:
    """Two Motor() instances on default config → two distinct ports."""
    cfg1 = MotorConfig(api_key="dummy", workspace_root=tmp_path / "m1",
                       console_log_enabled=False)
    cfg2 = MotorConfig(api_key="dummy", workspace_root=tmp_path / "m2",
                       console_log_enabled=False)
    async with Motor(cfg1) as m1, Motor(cfg2) as m2:
        assert m1._proxy.port != m2._proxy.port
