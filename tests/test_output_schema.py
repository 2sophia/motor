# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Output schema strict — Pydantic-validated structured_output via --json-schema.

Two live tests exercise the full agentic loop:

  - test_simple_verdict_schema: single-turn classification, no tools.
  - test_multi_turn_with_read_tool: agent must call Read on a seeded file
    AND return a schema-conforming JSON in the same run.

Both skip when ANTHROPIC_API_KEY is missing. Deterministic tests below
verify the wiring without an API call.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError


from sophia_motor import Motor, MotorConfig, RunTask


# ─────────────────────────────────────────────────────────────────────────
# Deterministic — no API call: verify --json-schema is wired into extra_args
# ─────────────────────────────────────────────────────────────────────────

class _ToySchema(BaseModel):
    verdict: Literal["A", "B"]
    score: float = Field(ge=0, le=1)


def test_output_schema_passed_to_cli_via_extra_args(tmp_path: Path) -> None:
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False))
    task = RunTask(prompt="test", output_schema=_ToySchema)
    opts = motor._build_sdk_options(
        task,
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
    )
    assert opts.extra_args is not None
    assert "json-schema" in opts.extra_args
    import json as _json
    schema = _json.loads(opts.extra_args["json-schema"])
    assert schema["properties"]["verdict"]["enum"] == ["A", "B"]
    # Pydantic v2 emits ge/le as exclusive bounds via numeric checks
    assert "score" in schema["properties"]


def test_output_schema_omitted_when_not_set(tmp_path: Path) -> None:
    motor = Motor(MotorConfig(api_key="dummy", workspace_root=tmp_path,
                              console_log_enabled=False))
    task = RunTask(prompt="test")  # no output_schema
    opts = motor._build_sdk_options(
        task,
        agent_cwd=tmp_path / "agent",
        claude_dir=tmp_path / ".claude",
        api_key="dummy",
    )
    if opts.extra_args:
        assert "json-schema" not in opts.extra_args


# ─────────────────────────────────────────────────────────────────────────
# Live — require ANTHROPIC_API_KEY
# ─────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set — set it to run live tests")
    return key


class Verdict(BaseModel):
    classification: Literal["CLEAR", "AMBIGUOUS"]
    rationale: str = Field(min_length=10)
    score: float = Field(ge=0, le=1)


async def test_simple_verdict_schema(api_key: str, tmp_path: Path) -> None:
    """Single-turn task, no tool calls, schema-conforming output."""
    config = MotorConfig(api_key=api_key, workspace_root=tmp_path,
                         console_log_enabled=False)
    async with Motor(config) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Classify whether this requirement is clear or ambiguous: "
                "'the bank publishes threshold rates on a quarterly basis'."
            ),
            tools=[],  # no tools — pure classification
            max_turns=3,
            output_schema=Verdict,
        ))
    assert not result.metadata.is_error, (
        f"run failed: {result.metadata.error_reason}"
    )
    assert result.output_data is not None, "structured_output not validated"
    assert isinstance(result.output_data, Verdict)
    assert result.output_data.classification in {"CLEAR", "AMBIGUOUS"}
    assert 0 <= result.output_data.score <= 1
    assert len(result.output_data.rationale) >= 10


class ControlMetadata(BaseModel):
    """Schema for a multi-turn extraction task — agent must read a file."""
    control_id: str = Field(pattern=r"^CTRL-\d{4}-\d{3}$")
    topic: str
    owner: str
    frequency: Literal["monthly", "quarterly", "annual"]
    cited_regulations: list[str]


async def test_multi_turn_with_read_tool(api_key: str, tmp_path: Path) -> None:
    """Agent reads a file via Read tool, then emits schema-strict JSON.

    Verifies that the agentic loop (multi-turn tool use + reasoning) and
    --json-schema (structured output validation) work TOGETHER in a
    single run.
    """
    config = MotorConfig(api_key=api_key, workspace_root=tmp_path,
                         console_log_enabled=False)
    sample = (
        "# Control CTRL-2026-042\n\n"
        "**Topic**: consumer-credit risk monitoring\n"
        "**Owner**: Risk Management\n"
        "**Frequency**: quarterly\n"
        "**Description**: The bank verifies any breach of regulatory "
        "threshold rates pursuant to relevant consumer-credit law via "
        "TEGM-style indicator calculation and forwarding of the reporting "
        "flow to the internal supervisory body.\n"
    )

    async with Motor(config) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Read attachments/control.md and extract the structured "
                "metadata of the control."
            ),
            tools=["Read"],
            attachments=[{"control.md": sample}],
            max_turns=8,
            output_schema=ControlMetadata,
        ))

    assert not result.metadata.is_error, (
        f"run failed: {result.metadata.error_reason}\nblocks={result.blocks}"
    )
    # Multi-turn proof — agent should have called Read at least once
    assert result.metadata.n_tool_calls >= 1, (
        f"expected at least 1 tool call, got {result.metadata.n_tool_calls}"
    )
    assert result.metadata.n_turns >= 2

    assert result.output_data is not None
    assert isinstance(result.output_data, ControlMetadata)
    # Content fidelity — agent extracted the right values from the file
    assert result.output_data.control_id == "CTRL-2026-042"
    assert result.output_data.frequency == "quarterly"
    assert any(
        "consumer" in ref.lower() or "credit" in ref.lower()
        for ref in result.output_data.cited_regulations
    )
