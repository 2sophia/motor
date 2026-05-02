# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Smoke tests for sophia-motor — require a real ANTHROPIC_API_KEY.

Tests skip cleanly when the key is missing so CI without secrets doesn't
fail. To run:

    ANTHROPIC_API_KEY=sk-ant-... pytest tests/test_motor_basic.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


from sophia_motor import Motor, MotorConfig, RunTask


SAMPLE_TEXT = """\
Acme Bank publishes quarterly threshold rates pursuant to article 2 of
the relevant consumer credit regulation. The threshold rate for Q1 2026
is set at 12.5% for consumer credit products.
"""


@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set — set it to run live motor tests")
    return key


async def test_motor_starts_proxy_and_stops_clean(tmp_path):
    """No live LLM call — just lifecycle: proxy boots, gets a port, stops."""
    config = MotorConfig(
        api_key="dummy-not-used-in-this-test",
        workspace_root=tmp_path,
        console_log_enabled=False,
    )
    async with Motor(config) as motor:
        assert motor._proxy is not None
        assert motor._proxy.base_url.startswith("http://127.0.0.1:")
        assert motor._proxy.port and motor._proxy.port > 0
    # after exit, proxy should be down
    assert motor._proxy is None


async def test_motor_runs_simple_read_task(api_key, tmp_path):
    """Live end-to-end with the real Anthropic API."""
    config = MotorConfig(
        api_key=api_key,
        workspace_root=tmp_path,
        console_log_enabled=False,
        proxy_dump_payloads=True,  # this test asserts on audit files
    )
    events_seen: list[str] = []

    async with Motor(config) as motor:
        @motor.on_event
        async def collect(event):
            events_seen.append(event.type)

        result = await motor.run(RunTask(
            prompt=(
                "Read attachments/sample.txt and produce a brief two-sentence "
                "summary of the regulatory content cited. Reply in English."
            ),
            tools=["Read"],
            attachments=[{"sample.txt": SAMPLE_TEXT}],
            max_turns=5,
        ))

    assert not result.metadata.is_error, (
        f"run failed: {result.metadata.error_reason}\nblocks={result.blocks}"
    )
    assert result.output_text and len(result.output_text) > 0
    assert "proxy_request" in events_seen
    assert "proxy_response" in events_seen
    assert "result" in events_seen

    # Audit dump must contain at least one request file
    request_files = list(result.audit_dir.glob("request_*.json"))
    assert request_files, f"no audit dump in {result.audit_dir}"

    # trace.json persisted
    assert (result.workspace_dir / "trace.json").exists()
    assert (result.workspace_dir / "input.json").exists()
