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
import sys
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, Field, ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sophia_motor import Motor, MotorConfig, RunTask  # noqa: E402


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
    verdetto: Literal["CHIARO", "AMBIGUO"]
    motivo: str = Field(min_length=10)
    punteggio: float = Field(ge=0, le=1)


async def test_simple_verdict_schema(api_key: str, tmp_path: Path) -> None:
    """Single-turn task, no tool calls, schema-conforming output."""
    config = MotorConfig(api_key=api_key, workspace_root=tmp_path,
                         console_log_enabled=False)
    async with Motor(config) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Valuta se questo requisito è chiaro o ambiguo: "
                "'la banca pubblica i tassi soglia trimestralmente'."
            ),
            tools=[],  # no tools — pure classification
            allowed_tools=[],
            max_turns=3,
            output_schema=Verdict,
        ))
    assert not result.metadata.is_error, (
        f"run failed: {result.metadata.error_reason}"
    )
    assert result.output_data is not None, "structured_output not validated"
    assert isinstance(result.output_data, Verdict)
    assert result.output_data.verdetto in {"CHIARO", "AMBIGUO"}
    assert 0 <= result.output_data.punteggio <= 1
    assert len(result.output_data.motivo) >= 10


class ControlloMetadata(BaseModel):
    """Schema for a multi-turn extraction task — agent must read a file."""
    controllo_id: str = Field(pattern=r"^CTRL-\d{4}-\d{3}$")
    tema: str
    owner: str
    periodicita: Literal["mensile", "trimestrale", "annuale"]
    normativa_citata: list[str]


async def test_multi_turn_with_read_tool(api_key: str, tmp_path: Path) -> None:
    """Agent reads a file via Read tool, then emits schema-strict JSON.

    Verifies that the agentic loop (multi-turn tool use + reasoning) and
    --json-schema (structured output validation) work TOGETHER in a
    single run — the use case for RGCI agent-based verdict.
    """
    config = MotorConfig(api_key=api_key, workspace_root=tmp_path,
                         console_log_enabled=False)
    sample = (
        "# Controllo CTRL-2026-042\n\n"
        "**Tema**: monitoraggio del rischio di credito al consumo\n"
        "**Owner**: Risk Management\n"
        "**Periodicità**: trimestrale\n"
        "**Descrizione**: La banca verifica il superamento dei tassi "
        "soglia ai sensi della legge 108/1996 attraverso il calcolo "
        "del TEGM e l'invio del flusso di reporting all'organo di "
        "vigilanza interno.\n"
    )

    async with Motor(config) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Leggi attachments/controllo.md ed estrai i metadati "
                "strutturati del controllo."
            ),
            tools=["Read"],
            allowed_tools=["Read"],
            attachments=[{"controllo.md": sample}],
            max_turns=8,
            output_schema=ControlloMetadata,
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
    assert isinstance(result.output_data, ControlloMetadata)
    # Content fidelity — agent extracted the right values from the file
    assert result.output_data.controllo_id == "CTRL-2026-042"
    assert result.output_data.periodicita == "trimestrale"
    assert any(
        "108/1996" in ref for ref in result.output_data.normativa_citata
    )
