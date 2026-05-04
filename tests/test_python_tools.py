# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Deterministic tests for the @tool decorator and the wiring layer.

Live integration with Claude (real /v1/messages roundtrip) lives in
`examples/python-tools/main.py` — we don't gate that on ANTHROPIC_API_KEY
inside pytest because the value of the live test is being able to watch
it tick on the terminal during dev.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from claude_agent_sdk import AgentDefinition

from sophia_motor import Motor, ToolContext, tool
from sophia_motor._python_tools import (
    _make_dispatcher,
    compile_python_tools,
    get_meta,
    normalize_run_tools,
    split_tools,
    validate_python_tools,
)
from sophia_motor.events import EventBus


# ─────────────────────────────────────────────────────────────────────
# Fixtures (module-level so `typing.get_type_hints` can resolve them)
# ─────────────────────────────────────────────────────────────────────

class _Inp(BaseModel):
    x: int

class _Out(BaseModel):
    y: int


class _Complex(BaseModel):
    name: str
    count: int = 1


# ─────────────────────────────────────────────────────────────────────
# Decorator forms
# ─────────────────────────────────────────────────────────────────────

def test_decorator_bare_form():
    @tool
    async def my_tool(args: _Inp) -> _Out:
        """Bare form description."""
        return _Out(y=args.x)

    meta = get_meta(my_tool)
    assert meta.name == "my_tool"
    assert meta.description == "Bare form description."
    assert meta.has_ctx is False
    assert meta.is_async is True


def test_decorator_motor_alias():
    @Motor.tool
    async def via_motor(args: _Inp) -> _Out:
        """Via Motor.tool."""
        return _Out(y=args.x)

    meta = get_meta(via_motor)
    assert meta.name == "via_motor"


def test_decorator_with_name_override():
    @tool(name="custom.id")
    async def my_tool(args: _Inp) -> _Out:
        """ignored by override."""
        return _Out(y=args.x)

    assert get_meta(my_tool).name == "custom.id"


def test_decorator_with_description_override():
    @tool(description="Override wins")
    async def my_tool(args: _Inp) -> _Out:
        """docstring loses"""
        return _Out(y=args.x)

    assert get_meta(my_tool).description == "Override wins"


def test_decorator_examples_appended():
    @tool(examples=[{"input": {"x": 1}, "output": {"y": 1}}])
    async def my_tool(args: _Inp) -> _Out:
        """Base."""
        return _Out(y=args.x)

    desc = get_meta(my_tool).description
    assert "Examples:" in desc
    assert '"x": 1' in desc


def test_decorator_sync_function():
    @tool
    def my_sync_tool(args: _Inp) -> _Out:
        """Sync."""
        return _Out(y=args.x)

    meta = get_meta(my_sync_tool)
    assert meta.is_async is False


def test_decorator_with_context():
    @tool
    async def with_ctx(args: _Inp, ctx: ToolContext) -> _Out:
        """Has ctx."""
        return _Out(y=args.x)

    meta = get_meta(with_ctx)
    assert meta.has_ctx is True
    assert meta.ctx_param_name == "ctx"


def test_decorator_context_with_custom_name():
    @tool
    async def with_named_ctx(args: _Inp, run_context: ToolContext) -> _Out:
        """Custom ctx name."""
        return _Out(y=args.x)

    meta = get_meta(with_named_ctx)
    assert meta.has_ctx is True
    assert meta.ctx_param_name == "run_context"


# ─────────────────────────────────────────────────────────────────────
# Decorator validation errors
# ─────────────────────────────────────────────────────────────────────

def test_decorator_rejects_no_params():
    with pytest.raises(TypeError, match="must have at least one parameter"):
        @tool
        async def no_params() -> _Out:
            """no params."""
            return _Out(y=0)


def test_decorator_rejects_unannotated_first_param():
    with pytest.raises(TypeError, match="must be type-annotated"):
        @tool
        async def bad(args) -> _Out:
            """no annotation."""
            return _Out(y=0)


def test_decorator_rejects_non_pydantic_first_param():
    with pytest.raises(TypeError, match="Pydantic BaseModel"):
        @tool
        async def bad(args: dict) -> _Out:
            """dict not BaseModel."""
            return _Out(y=0)


def test_decorator_rejects_missing_description():
    with pytest.raises(ValueError, match="must have a description"):
        @tool
        async def bad(args: _Inp) -> _Out:
            return _Out(y=0)


# ─────────────────────────────────────────────────────────────────────
# Schema derivation (Pydantic → JSON Schema)
# ─────────────────────────────────────────────────────────────────────

def test_schema_derived_from_pydantic():
    @tool
    async def my_tool(args: _Complex) -> _Out:
        """Schema check."""
        return _Out(y=args.count)

    schema = get_meta(my_tool).input_schema
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "count" in schema["properties"]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["count"]["type"] == "integer"


# ─────────────────────────────────────────────────────────────────────
# split_tools / validate_python_tools
# ─────────────────────────────────────────────────────────────────────

def test_split_tools_separates_strings_and_callables():
    @tool
    async def f1(args: _Inp) -> _Out:
        """f1."""
        return _Out(y=args.x)

    @tool
    async def f2(args: _Inp) -> _Out:
        """f2."""
        return _Out(y=args.x)

    strings, callables = split_tools(["Read", f1, "Glob", f2])
    assert strings == ["Read", "Glob"]
    assert callables == [f1, f2]


def test_split_tools_rejects_unknown_type():
    with pytest.raises(TypeError, match="must be str.*or callable"):
        split_tools(["Read", 42])


def test_validate_rejects_undecorated_callable():
    async def not_decorated(args):
        return {}

    with pytest.raises(TypeError, match="not decorated"):
        validate_python_tools([not_decorated])


def test_validate_rejects_builtin_name_collision():
    @tool(name="Read")
    async def naughty(args: _Inp) -> _Out:
        """Collides with built-in."""
        return _Out(y=args.x)

    with pytest.raises(ValueError, match="collides with a built-in"):
        validate_python_tools([naughty])


def test_validate_rejects_duplicate_names():
    @tool(name="dup")
    async def a(args: _Inp) -> _Out:
        """a."""
        return _Out(y=args.x)

    @tool(name="dup")
    async def b(args: _Inp) -> _Out:
        """b."""
        return _Out(y=args.x)

    with pytest.raises(ValueError, match="Duplicate"):
        validate_python_tools([a, b])


# ─────────────────────────────────────────────────────────────────────
# Dispatcher behaviour (integration without the SDK CLI subprocess)
# ─────────────────────────────────────────────────────────────────────

def _make(meta_callable, *, tmp_path: Path, run_id: str, bus=None):
    """Helper: build a dispatcher closure for direct unit testing.

    Bypasses the MCP server (which is SDK internals). The dispatcher is
    where the motor's logic actually lives — input validation, ctx
    injection, audit dump, event emission, error handling.
    """
    metas = validate_python_tools([meta_callable])
    return _make_dispatcher(
        metas[0],
        run_id=run_id,
        audit_dir=tmp_path / "audit",
        agent_cwd=tmp_path / "cwd",
        event_bus=bus or EventBus(),
        dump_audit=True,
    )


@pytest.mark.asyncio
async def test_dispatcher_input_validation_error(tmp_path: Path):
    @tool
    async def doubler(args: _Inp) -> _Out:
        """Doubles."""
        return _Out(y=args.x * 2)

    dispatch = _make(doubler, tmp_path=tmp_path, run_id="r1")
    out = await dispatch({"x": "not-an-int"})
    assert out.get("is_error") is True
    assert "input validation failed" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_happy_path_audit_and_event(tmp_path: Path):
    @tool
    async def doubler(args: _Inp) -> _Out:
        """Doubles."""
        return _Out(y=args.x * 2)

    bus = EventBus()
    captured = []
    bus.on_event(captured.append)
    dispatch = _make(doubler, tmp_path=tmp_path, run_id="r2", bus=bus)

    out = await dispatch({"x": 21})
    assert out["content"][0]["text"] == '{"y":42}'
    assert "is_error" not in out

    audit_files = list((tmp_path / "audit").glob("tool_doubler_*.json"))
    assert len(audit_files) == 1
    payload = json.loads(audit_files[0].read_text())
    assert payload["tool"] == "doubler"
    assert payload["input"] == {"x": 21}
    assert payload["output"] == {"y": 42}
    assert payload["error"] is None
    assert isinstance(payload["duration_ms"], int)

    assert any(e.type == "python_tool_call" for e in captured)
    ev = next(e for e in captured if e.type == "python_tool_call")
    assert ev.payload["name"] == "doubler"
    assert ev.payload["ok"] is True


@pytest.mark.asyncio
async def test_dispatcher_exception_caught_and_reported(tmp_path: Path):
    @tool
    async def boom(args: _Inp) -> _Out:
        """Always fails."""
        raise RuntimeError("kaboom")

    dispatch = _make(boom, tmp_path=tmp_path, run_id="r3")
    out = await dispatch({"x": 1})
    assert out.get("is_error") is True
    assert "RuntimeError" in out["content"][0]["text"]
    assert "kaboom" in out["content"][0]["text"]


@pytest.mark.asyncio
async def test_dispatcher_sync_function_wrapped(tmp_path: Path):
    @tool
    def syncer(args: _Inp) -> _Out:
        """Sync impl."""
        return _Out(y=args.x + 100)

    dispatch = _make(syncer, tmp_path=tmp_path, run_id="r4")
    out = await dispatch({"x": 5})
    assert out["content"][0]["text"] == '{"y":105}'


@pytest.mark.asyncio
async def test_dispatcher_context_injected(tmp_path: Path):
    seen_run_id: list[str] = []

    @tool
    async def needs_ctx(args: _Inp, ctx: ToolContext) -> _Out:
        """Uses ctx."""
        seen_run_id.append(ctx.run_id)
        ctx.outputs_dir.mkdir(parents=True, exist_ok=True)
        return _Out(y=args.x)

    dispatch = _make(needs_ctx, tmp_path=tmp_path, run_id="r5")
    out = await dispatch({"x": 7})
    assert "is_error" not in out
    assert seen_run_id == ["r5"]
    assert (tmp_path / "cwd" / "outputs").is_dir()


# ─────────────────────────────────────────────────────────────────────
# compile_python_tools edge cases
# ─────────────────────────────────────────────────────────────────────

def test_compile_empty_returns_no_server(tmp_path: Path):
    server, names = compile_python_tools(
        [],
        run_id="empty",
        audit_dir=tmp_path,
        agent_cwd=tmp_path,
        event_bus=EventBus(),
    )
    assert server is None
    assert names == []


def test_normalize_no_agents_passes_through_task_split():
    @tool
    async def f(args: _Inp) -> _Out:
        """f."""
        return _Out(y=args.x)

    metas, parent, agents = normalize_run_tools(["Read", f], None)
    assert {m.name for m in metas} == {"f"}
    assert parent == ["Read", "mcp__sophia__f"]
    assert agents == {}


def test_normalize_agent_tools_none_inherits():
    @tool
    async def f(args: _Inp) -> _Out:
        """f."""
        return _Out(y=args.x)

    ad = AgentDefinition(description="d", prompt="p", tools=None)
    metas, parent, agents = normalize_run_tools([f], {"specialist": ad})
    assert {m.name for m in metas} == {"f"}
    assert parent == ["mcp__sophia__f"]
    # tools=None on the AgentDefinition is preserved as-is — that's what
    # triggers the SDK's inheritance path.
    assert agents["specialist"].tools is None


def test_normalize_agent_callable_rewritten_to_prefix():
    @tool
    async def writer(args: _Inp) -> _Out:
        """writer."""
        return _Out(y=args.x)

    ad = AgentDefinition(description="d", prompt="p", tools=[writer])
    metas, parent, agents = normalize_run_tools(None, {"w": ad})
    assert {m.name for m in metas} == {"writer"}
    # Parent had None → still None.
    assert parent is None
    # Agent's callable was rewritten to the prefixed string.
    assert agents["w"].tools == ["mcp__sophia__writer"]


def test_normalize_dedupes_callable_shared_between_parent_and_agent():
    @tool
    async def shared(args: _Inp) -> _Out:
        """shared."""
        return _Out(y=args.x)

    ad = AgentDefinition(description="d", prompt="p", tools=[shared])
    metas, parent, agents = normalize_run_tools([shared], {"a": ad})
    # Even though `shared` appears in both parent and agent, it's
    # registered once in the MCP server.
    assert len(metas) == 1
    assert metas[0].name == "shared"
    assert parent == ["mcp__sophia__shared"]
    assert agents["a"].tools == ["mcp__sophia__shared"]


def test_normalize_different_callables_same_name_collide():
    @tool(name="conflict")
    async def a(args: _Inp) -> _Out:
        """a."""
        return _Out(y=args.x)

    @tool(name="conflict")
    async def b(args: _Inp) -> _Out:
        """b."""
        return _Out(y=args.x)

    ad = AgentDefinition(description="d", prompt="p", tools=[b])
    with pytest.raises(ValueError, match="Duplicate @tool name 'conflict'"):
        normalize_run_tools([a], {"x": ad})


def test_normalize_agent_only_callable_added_to_metas():
    @tool
    async def parent_tool(args: _Inp) -> _Out:
        """parent."""
        return _Out(y=args.x)

    @tool
    async def agent_only(args: _Inp) -> _Out:
        """agent only."""
        return _Out(y=args.x)

    ad = AgentDefinition(description="d", prompt="p", tools=[agent_only])
    metas, parent, agents = normalize_run_tools(
        [parent_tool], {"specialist": ad},
    )
    # Both tools land in the MCP server even though parent never sees agent_only.
    assert {m.name for m in metas} == {"parent_tool", "agent_only"}
    # Parent only sees parent_tool.
    assert parent == ["mcp__sophia__parent_tool"]
    # Subagent only sees agent_only.
    assert agents["specialist"].tools == ["mcp__sophia__agent_only"]


def test_normalize_agent_string_entries_pass_through():
    ad = AgentDefinition(
        description="d", prompt="p",
        tools=["Read", "mcp__sophia__pre_prefixed"],
    )
    metas, parent, agents = normalize_run_tools(None, {"a": ad})
    assert metas == []
    assert parent is None
    # Both string entries pass through verbatim.
    assert agents["a"].tools == ["Read", "mcp__sophia__pre_prefixed"]


def test_normalize_agent_rejects_invalid_entry_type():
    ad = AgentDefinition(description="d", prompt="p", tools=[42])
    with pytest.raises(TypeError, match="must be str or callable"):
        normalize_run_tools(None, {"a": ad})


def test_normalize_agent_callable_collides_with_builtin_name():
    @tool(name="Read")
    async def naughty(args: _Inp) -> _Out:
        """collides."""
        return _Out(y=args.x)

    ad = AgentDefinition(description="d", prompt="p", tools=[naughty])
    with pytest.raises(ValueError, match="collides with a built-in"):
        normalize_run_tools(None, {"a": ad})


def test_prefixed_names_use_default_server(tmp_path: Path):
    @tool
    async def f(args: _Inp) -> _Out:
        """f."""
        return _Out(y=args.x)

    metas = validate_python_tools([f])
    _, names = compile_python_tools(
        metas,
        run_id="r1",
        audit_dir=tmp_path / "a",
        agent_cwd=tmp_path / "c",
        event_bus=EventBus(),
    )
    assert names == ["mcp__sophia__f"]
