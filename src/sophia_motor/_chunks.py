# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Stream chunks emitted by `Motor.stream(task)`.

Discriminated union on `type`. Consumers iterate with:

    async for chunk in motor.stream(task):
        if chunk.type == "text_delta":
            print(chunk.text, end="", flush=True)
        elif chunk.type == "tool_use_start":
            print(f"[{chunk.tool}] starting…")
        elif chunk.type == "done":
            return chunk.result

The stream always ends with a single `DoneChunk` carrying the final
`RunResult`. Errors during the loop surface as `ErrorChunk` followed by
`DoneChunk` with `result.metadata.is_error=True`.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from ._models import RunResult


class _ChunkBase(BaseModel):
    """Common base. Each subclass narrows `type` to a unique Literal."""
    model_config = {"extra": "forbid"}


# ── lifecycle ────────────────────────────────────────────────────────

class RunStartedChunk(_ChunkBase):
    type: Literal["run_started"] = "run_started"
    run_id: str
    model: str
    prompt_preview: str
    max_turns: int


class InitChunk(_ChunkBase):
    """Emitted once per run when the CLI subprocess reports the SDK session_id."""
    type: Literal["init"] = "init"
    session_id: str | None


# ── text + thinking deltas (raw API path) ────────────────────────────

class TextDeltaChunk(_ChunkBase):
    """A `text_delta` from the streaming API. Render to UI with `print(chunk.text, end="")`."""
    type: Literal["text_delta"] = "text_delta"
    text: str


class ThinkingDeltaChunk(_ChunkBase):
    """A `thinking_delta` chunk. Surface only if your UI renders reasoning."""
    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


# ── tool_use lifecycle ───────────────────────────────────────────────

class ToolUseStartChunk(_ChunkBase):
    """Opens a tool_use content block. Show `[tool] …` placeholder in UI."""
    type: Literal["tool_use_start"] = "tool_use_start"
    tool_use_id: str
    tool: str
    index: int


class ToolUseDeltaChunk(_ChunkBase):
    """Stream chunk of an `input_json_delta`.

    `partial_json` is the **raw fragment** from the API.
    `extracted` is a best-effort partial parse (may be empty until enough
    tokens have accumulated). For `Write`/`Edit`, this lets a UI render a
    live filename + content preview before the call commits.
    """
    type: Literal["tool_use_delta"] = "tool_use_delta"
    tool_use_id: str
    tool: str
    partial_json: str
    extracted: dict[str, Any]
    index: int


class ToolUseCompleteChunk(_ChunkBase):
    """Closes a tool_use content block. `input` is not yet finalized here —
    wait for `ToolUseFinalizedChunk` for the canonical input dict."""
    type: Literal["tool_use_complete"] = "tool_use_complete"
    tool_use_id: str
    tool: str


class ToolUseFinalizedChunk(_ChunkBase):
    """The fully-formed tool_use input as the SDK reconstructed it.

    Emitted after `ToolUseCompleteChunk` once the AssistantMessage carrying
    the canonical block arrives. Use this (not the deltas) for any logic
    that depends on the exact input the model sent.
    """
    type: Literal["tool_use_finalized"] = "tool_use_finalized"
    tool_use_id: str
    tool: str
    input: dict[str, Any]


class ToolResultChunk(_ChunkBase):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    is_error: bool
    preview: str


# ── fallback paths ───────────────────────────────────────────────────

class TextBlockChunk(_ChunkBase):
    """Full TextBlock arriving without prior `text_delta`s — fallback for
    backends/configs that don't stream partials. Treat as the entire text
    for that block; do NOT concatenate with prior deltas."""
    type: Literal["text_block"] = "text_block"
    text: str


class ThinkingBlockChunk(_ChunkBase):
    type: Literal["thinking_block"] = "thinking_block"
    text: str


# ── error + completion ───────────────────────────────────────────────

class ErrorChunk(_ChunkBase):
    """Non-fatal error during streaming. A `DoneChunk` will still follow."""
    type: Literal["error"] = "error"
    message: str


class DoneChunk(_ChunkBase):
    """Terminal chunk. Always the last item yielded by `motor.stream(task)`,
    including on error. Carries the same `RunResult` that `motor.run(task)`
    would have returned."""
    type: Literal["done"] = "done"
    result: RunResult


# ── discriminated union ──────────────────────────────────────────────

StreamChunk = Annotated[
    Union[
        RunStartedChunk,
        InitChunk,
        TextDeltaChunk,
        ThinkingDeltaChunk,
        ToolUseStartChunk,
        ToolUseDeltaChunk,
        ToolUseCompleteChunk,
        ToolUseFinalizedChunk,
        ToolResultChunk,
        TextBlockChunk,
        ThinkingBlockChunk,
        ErrorChunk,
        DoneChunk,
    ],
    Field(discriminator="type"),
]
