# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Verify that transient network failures are mapped to clean responses
instead of leaking httpx tracebacks through the ASGI middleware.

The SDK CLI retries above us when it gets a 502 or a stream that ends
with an `event: error`, so the user never notices these — but without
catching them in the proxy, every glitch produces a multi-screen
traceback in the terminal that scares whoever is reading the logs.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sophia_motor.config import MotorConfig
from sophia_motor.events import EventBus
from sophia_motor.proxy import ProxyServer


def _make_proxy(tmp_path: Path) -> ProxyServer:
    proxy = ProxyServer(MotorConfig(api_key="test-key"), EventBus())
    audit = tmp_path / "audit"
    audit.mkdir()
    proxy.register_run("r1", audit)
    return proxy


@pytest.mark.asyncio
async def test_non_streaming_connect_timeout_returns_502(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **kw):
            raise httpx.ConnectTimeout("simulated timeout")

    # Build the test client BEFORE patching — otherwise our own client
    # becomes the mock too.
    transport = httpx.ASGITransport(app=proxy.app)
    test_client = httpx.AsyncClient(transport=transport, base_url="http://test")
    monkeypatch.setattr("sophia_motor.proxy.httpx.AsyncClient", _MockClient)

    async with test_client:
        r = await test_client.post(
            "/run/r1/v1/messages",
            json={
                "model": "claude-opus-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )

    assert r.status_code == 502
    body = r.json()
    assert body["error"]["type"] == "upstream_error"
    assert "ConnectTimeout" in body["error"]["message"]


@pytest.mark.asyncio
async def test_streaming_connect_error_yields_clean_error_chunk(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)

    class _CM:
        async def __aenter__(self):
            raise httpx.ConnectError("simulated dns failure")

        async def __aexit__(self, *a):
            return None

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url, *a, **kw):
            return _CM()

    transport = httpx.ASGITransport(app=proxy.app)
    test_client = httpx.AsyncClient(transport=transport, base_url="http://test")
    monkeypatch.setattr("sophia_motor.proxy.httpx.AsyncClient", _MockClient)

    async with test_client:
        r = await test_client.post(
            "/run/r1/v1/messages",
            json={
                "model": "claude-opus-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )

    # Streaming response was already committed to 200 status before the
    # network failed. We can only signal via a synthetic SSE error event.
    assert r.status_code == 200
    text = r.text
    assert "event: error" in text
    assert "upstream_error" in text
    assert "ConnectError" in text


@pytest.mark.asyncio
async def test_non_streaming_warning_logged(tmp_path, monkeypatch):
    proxy = _make_proxy(tmp_path)

    captured: list[tuple[str, str]] = []

    @proxy.events.on_log
    def grab(record):
        captured.append((record.level, record.message))

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **kw):
            raise httpx.ReadTimeout("simulated read timeout")

    transport = httpx.ASGITransport(app=proxy.app)
    test_client = httpx.AsyncClient(transport=transport, base_url="http://test")
    monkeypatch.setattr("sophia_motor.proxy.httpx.AsyncClient", _MockClient)

    async with test_client:
        await test_client.post(
            "/run/r1/v1/messages",
            json={
                "model": "claude-opus-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )

    warnings = [m for level, m in captured if level == "WARNING"]
    assert any("upstream unreachable" in m for m in warnings)
    assert any("ReadTimeout" in m for m in warnings)
