# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for streaming primitives (no API key required).

Live coverage of `motor.stream()` lives in `test_motor_basic.py`.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from sophia_motor import (
    DoneChunk,
    ErrorChunk,
    InitChunk,
    RunStartedChunk,
    StreamChunk,
    TextDeltaChunk,
    ToolUseDeltaChunk,
    ToolUseStartChunk,
)
from sophia_motor._partial_json import parse_partial_tool_input


# ─── partial JSON parser ───────────────────────────────────────────────

def test_partial_json_complete_valid_json():
    out = parse_partial_tool_input('{"file_path": "outputs/r.md"}', "Write")
    assert out == {"file_path": "outputs/r.md"}


def test_partial_json_truncated_value_heuristic_close():
    # missing closing quote + brace
    out = parse_partial_tool_input('{"file_path": "outputs/repor', "Write")
    assert out == {"file_path": "outputs/repor"}


def test_partial_json_two_fields_one_truncated():
    out = parse_partial_tool_input(
        '{"file_path": "outputs/r.md", "content": "# Tito',
        "Write",
    )
    assert out["file_path"] == "outputs/r.md"
    assert out["content"].startswith("# Tito")


def test_partial_json_empty_input_returns_empty_dict():
    assert parse_partial_tool_input("", "Read") == {}


def test_partial_json_unknown_tool_falls_back_to_default_fields():
    # Tool not in the registry → tries file_path/content/command
    out = parse_partial_tool_input(
        '{"command": "ls -la', "WhateverTool",
    )
    assert out.get("command", "").startswith("ls -la")


def test_partial_json_no_match_returns_empty():
    # Garbage input that matches none of the known fields
    out = parse_partial_tool_input("{not json at all", "Read")
    assert out == {}


def test_partial_json_handles_escaped_chars_in_value():
    out = parse_partial_tool_input(
        r'{"content": "line1\nline2\tend"}',
        "Write",
    )
    assert out == {"content": "line1\nline2\tend"}


# ─── StreamChunk discriminated union ──────────────────────────────────

def test_stream_chunk_validates_text_delta():
    adapter = TypeAdapter(StreamChunk)
    chunk = adapter.validate_python({"type": "text_delta", "text": "hi"})
    assert isinstance(chunk, TextDeltaChunk)
    assert chunk.text == "hi"


def test_stream_chunk_validates_tool_use_start():
    adapter = TypeAdapter(StreamChunk)
    chunk = adapter.validate_python({
        "type": "tool_use_start",
        "tool_use_id": "toolu_01",
        "tool": "Read",
        "index": 1,
    })
    assert isinstance(chunk, ToolUseStartChunk)
    assert chunk.tool == "Read"


def test_stream_chunk_validates_tool_use_delta_with_extracted():
    adapter = TypeAdapter(StreamChunk)
    chunk = adapter.validate_python({
        "type": "tool_use_delta",
        "tool_use_id": "toolu_01",
        "tool": "Write",
        "partial_json": '{"file_path": "outputs/x',
        "extracted": {"file_path": "outputs/x"},
        "index": 0,
    })
    assert isinstance(chunk, ToolUseDeltaChunk)
    assert chunk.extracted == {"file_path": "outputs/x"}


def test_stream_chunk_rejects_unknown_type():
    adapter = TypeAdapter(StreamChunk)
    with pytest.raises(Exception):
        adapter.validate_python({"type": "totally_made_up", "foo": 1})


def test_run_started_chunk_default_type():
    chunk = RunStartedChunk(
        run_id="run-x",
        model="opus",
        prompt_preview="hi",
        max_turns=10,
    )
    assert chunk.type == "run_started"


def test_init_chunk_session_id_optional():
    chunk = InitChunk(session_id=None)
    assert chunk.session_id is None
    assert chunk.type == "init"


def test_error_chunk():
    chunk = ErrorChunk(message="upstream timeout")
    assert chunk.type == "error"


# ─── run() consumes stream() — verified via live test ─────────────────
# The live test in test_motor_basic.py already exercises the full
# run/stream wiring against the real API. Mocking ClaudeSDKClient just to
# replicate the wiring would couple tests to SDK internals without adding
# coverage we don't already have.
