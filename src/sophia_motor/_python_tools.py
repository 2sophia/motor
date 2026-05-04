# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Python tools — Pythonic functions exposed to Claude as in-process MCP.

The motor wraps the SDK's `@tool` + `create_sdk_mcp_server` into a single
flow: decorate a function with `@tool`, pass it in `default_tools` (or
`RunTask.tools`) alongside built-in tool name strings, and the motor
mounts everything as one MCP server named `sophia`.

Schema is derived from a Pydantic model on the first positional parameter.
A second parameter annotated `ToolContext` (any name) is auto-injected
with run-scoped paths. Sync functions are accepted (wrapped in
`asyncio.to_thread`). Each call is dumped to `<run>/audit/` and emitted
on the EventBus as `Event(type="python_tool_call", ...)`.

The model sees tools as `mcp__sophia__<name>` — that is the standard MCP
naming convention and we deliberately don't rewrite it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import time
import traceback
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool as _sdk_tool
from pydantic import BaseModel

from .events import Event, EventBus


# Built-in tool names the SDK CLI provides natively. We refuse callable
# names that would collide.
_BUILTIN_TOOL_NAMES = frozenset({
    "Read", "Edit", "Write", "Glob", "Grep", "Bash",
    "WebFetch", "WebSearch", "Agent", "Skill",
    "TodoWrite", "AskUserQuestion",
    "EnterPlanMode", "ExitPlanMode", "EnterWorktree", "ExitWorktree",
    "CronCreate", "CronDelete", "CronList",
    "Monitor", "PushNotification", "ScheduleWakeup",
    "NotebookEdit", "RemoteTrigger", "TaskOutput", "TaskStop",
    "ToolSearch", "SendMessage", "TaskCreate", "TaskGet",
    "TaskList", "TaskUpdate", "TeamCreate", "TeamDelete",
})

_META_ATTR = "_motor_tool_meta"
DEFAULT_SERVER_NAME = "sophia"


# ─────────────────────────────────────────────────────────────────────
# Public dataclasses
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolContext:
    """Run-scoped context, optionally injected into a tool function.

    Declare a second parameter annotated as `ToolContext` (any name) and
    the motor populates it before each call. Useful for tools that need
    to write into `<run>/agent_cwd/outputs/`, read attachments, or write
    debug data alongside the audit dump.
    """
    run_id: str
    agent_cwd: Path
    outputs_dir: Path
    attachments_dir: Path
    audit_dir: Path


@dataclass(frozen=True)
class ToolMeta:
    """Metadata attached by `@tool`. Public for introspection / testing."""
    name: str
    description: str
    input_schema: dict          # JSON Schema dict (from Pydantic)
    input_model: type           # Pydantic BaseModel subclass
    has_ctx: bool
    ctx_param_name: Optional[str]
    is_async: bool
    fn: Callable                # original undecorated function


# ─────────────────────────────────────────────────────────────────────
# @tool decorator
# ─────────────────────────────────────────────────────────────────────

def tool(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    examples: Optional[list[dict]] = None,
):
    """Mark a Python function as a tool callable by the model.

    Forms accepted:

        @tool
        async def my_tool(args: MyInput) -> MyOutput: ...

        @tool(name="custom.name")
        async def my_tool(args: MyInput) -> MyOutput: ...

        @tool(description="Override docstring for the model.")
        async def my_tool(args: MyInput) -> MyOutput: ...

        @tool(examples=[{"input": {...}, "output": {...}}, ...])
        async def my_tool(args: MyInput) -> MyOutput: ...

    Sync functions are accepted; the motor wraps them in
    `asyncio.to_thread` automatically.
    """
    def decorate(f: Callable) -> Callable:
        meta = _build_meta(
            f,
            name_override=name,
            description_override=description,
            examples=examples,
        )
        setattr(f, _META_ATTR, meta)
        return f

    if fn is None:
        # `@tool(...)` form
        return decorate
    # `@tool` bare form
    return decorate(fn)


def _build_meta(
    fn: Callable,
    *,
    name_override: Optional[str],
    description_override: Optional[str],
    examples: Optional[list[dict]],
) -> ToolMeta:
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())

    if not params:
        raise TypeError(
            f"@tool function '{fn.__name__}' must have at least one parameter "
            f"(args: <Pydantic BaseModel>)."
        )

    # Resolve string annotations (PEP 563 / `from __future__ import annotations`).
    # Falls back to raw .annotation if the function lives in a module without
    # importable globals (rare, e.g. dynamically generated lambdas).
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    first = params[0]
    if first.annotation is inspect.Parameter.empty:
        raise TypeError(
            f"@tool function '{fn.__name__}' first parameter must be type-"
            f"annotated as a Pydantic BaseModel subclass "
            f"(e.g. `args: MyInputModel`)."
        )

    input_type = hints.get(first.name, first.annotation)
    if not (isinstance(input_type, type) and issubclass(input_type, BaseModel)):
        raise TypeError(
            f"@tool function '{fn.__name__}' first parameter must be a "
            f"Pydantic BaseModel subclass, got {input_type!r}."
        )

    # ToolContext detection — any non-first param resolving to `ToolContext`.
    has_ctx = False
    ctx_param_name: Optional[str] = None
    for p in params[1:]:
        ptype = hints.get(p.name, p.annotation)
        if ptype is ToolContext:
            has_ctx = True
            ctx_param_name = p.name
            break

    final_name = name_override or fn.__name__
    final_desc = (description_override or (fn.__doc__ or "")).strip()

    if examples:
        ex_lines = ["", "Examples:"]
        for ex in examples:
            ex_in = json.dumps(ex.get("input"), default=str, ensure_ascii=False)
            ex_out = json.dumps(ex.get("output"), default=str, ensure_ascii=False)
            ex_lines.append(f"  input:  {ex_in}")
            ex_lines.append(f"  output: {ex_out}")
        final_desc = (final_desc + "\n" + "\n".join(ex_lines)).strip()

    if not final_desc:
        raise ValueError(
            f"@tool function '{fn.__name__}' must have a description: "
            f"either add a docstring or pass description=... to @tool."
        )

    input_schema = input_type.model_json_schema()
    is_async = asyncio.iscoroutinefunction(fn)

    return ToolMeta(
        name=final_name,
        description=final_desc,
        input_schema=input_schema,
        input_model=input_type,
        has_ctx=has_ctx,
        ctx_param_name=ctx_param_name,
        is_async=is_async,
        fn=fn,
    )


def get_meta(callable_obj: Callable) -> ToolMeta:
    """Extract ToolMeta from a decorated callable. Raises if missing."""
    meta = getattr(callable_obj, _META_ATTR, None)
    if meta is None:
        raise TypeError(
            f"{callable_obj!r} is not decorated with @tool. Add `@tool` "
            f"(or `@Motor.tool`) above the function definition before "
            f"passing it to default_tools / RunTask.tools."
        )
    return meta


# ─────────────────────────────────────────────────────────────────────
# List splitting + validation (called at config / run time)
# ─────────────────────────────────────────────────────────────────────

def serialize_tools_list(items: Optional[list]) -> Optional[list[str]]:
    """Render a heterogeneous tool list as JSON-serializable strings.

    Used by `_persist_input` to dump RunTask.tools into <run>/input.json.
    Callables become `mcp__sophia__<name>` (matching what the model sees);
    strings pass through unchanged. Undecorated callables fall back to repr.
    """
    if items is None:
        return None
    out: list[str] = []
    for it in items:
        if isinstance(it, str):
            out.append(it)
            continue
        meta = getattr(it, _META_ATTR, None)
        if meta is not None:
            out.append(f"mcp__{DEFAULT_SERVER_NAME}__{meta.name}")
        else:
            out.append(repr(it))
    return out


def split_tools(items: list) -> tuple[list[str], list[Callable]]:
    """Split a heterogeneous tool list into (string_names, callables)."""
    strings: list[str] = []
    callables: list[Callable] = []
    for it in items:
        if isinstance(it, str):
            strings.append(it)
        elif callable(it):
            callables.append(it)
        else:
            raise TypeError(
                f"tool list entry must be str (built-in tool name) or "
                f"callable (@tool function), got {type(it).__name__}: {it!r}"
            )
    return strings, callables


def validate_python_tools(callables: list[Callable]) -> list[ToolMeta]:
    """Validate decorator presence + name collisions. Returns ordered metas.

    Raises immediately on:
      - missing @tool decorator
      - name collision with a built-in tool (Read, Bash, ...)
      - duplicate name within the same list
    """
    metas: list[ToolMeta] = []
    seen: set[str] = set()
    for c in callables:
        meta = get_meta(c)
        if meta.name in _BUILTIN_TOOL_NAMES:
            raise ValueError(
                f"@tool '{meta.name}' collides with a built-in tool name. "
                f"Rename via @tool(name='...')."
            )
        if meta.name in seen:
            raise ValueError(
                f"Duplicate @tool name '{meta.name}' in the same list. "
                f"Each Python tool must have a unique name."
            )
        seen.add(meta.name)
        metas.append(meta)
    return metas


def normalize_run_tools(
    task_tools: Optional[list],
    agents: Optional[dict[str, Any]],
) -> tuple[
    list[ToolMeta],            # all metas to mount in the run's MCP server
    Optional[list[str]],       # parent tools list (str + prefixed callables)
    dict[str, Any],            # agents with `tools` field normalized
]:
    """Single entry point for the motor — collect EVERY callable referenced
    in the run (parent task + every agent), dedupe by `meta.name`, validate,
    and produce:

      - the union of `ToolMeta` to register in the in-process MCP server
      - the parent's tool list with callables rewritten to `mcp__sophia__X`
      - a copy of `agents` whose `AgentDefinition.tools` are likewise rewritten

    Same callable referenced by parent + multiple agents → one entry, one
    dispatcher. Different callables sharing a `meta.name` → ValueError.
    Callable in `AgentDefinition.tools` that's NOT in the parent gets added
    to the server anyway (agents can have their own tools the parent never
    sees — that's the whole point of restricting subagent capabilities).
    """
    # 1. Parent split.
    if task_tools is None:
        parent_strings_only: list[str] = []
        parent_callables: list[Callable] = []
        parent_has_explicit = False
    else:
        ps, pc = split_tools(task_tools)
        parent_strings_only = list(ps)
        parent_callables = list(pc)
        parent_has_explicit = True

    # 2. Agents split — preserve the source (str | Callable) entries so
    #    we can rebuild AgentDefinition.tools with prefixed names while
    #    keeping the original ordering.
    per_agent_entries: dict[str, list] = {}
    if agents:
        for agent_name, ad in agents.items():
            ad_tools = getattr(ad, "tools", None)
            if ad_tools is None:
                # `tools=None` on a subagent means "inherit from the SDK
                # ancestry". Pass through, no work to do.
                continue
            # Validate type-correctness of each entry.
            for entry in ad_tools:
                if not (isinstance(entry, str) or callable(entry)):
                    raise TypeError(
                        f"AgentDefinition '{agent_name}'.tools entry must "
                        f"be str or callable, got {type(entry).__name__}: {entry!r}"
                    )
            per_agent_entries[agent_name] = list(ad_tools)

    # 3. Dedupe callables by `meta.name`. We track:
    #      - by-id (Python identity) to skip cheap repeats
    #      - by-name (semantic identity) to detect collisions across sources
    seen_by_id: dict[int, str] = {}
    seen_by_name: dict[str, ToolMeta] = {}

    def _register(c: Callable, source: str) -> ToolMeta:
        meta = get_meta(c)
        if meta.name in _BUILTIN_TOOL_NAMES:
            raise ValueError(
                f"@tool '{meta.name}' (from {source}) collides with a "
                f"built-in tool name. Rename via @tool(name='...')."
            )
        existing = seen_by_name.get(meta.name)
        if existing is not None and existing is not meta:
            raise ValueError(
                f"Duplicate @tool name '{meta.name}' across the run "
                f"(parent + agents) bound to different function objects. "
                f"Each Python tool must have a unique name."
            )
        seen_by_id[id(c)] = meta.name
        seen_by_name[meta.name] = meta
        return meta

    for c in parent_callables:
        _register(c, "task.tools")
    for agent_name, entries in per_agent_entries.items():
        for entry in entries:
            if callable(entry) and not isinstance(entry, str):
                _register(entry, f"agents['{agent_name}'].tools")

    all_metas = list(seen_by_name.values())

    # 4. Build parent's effective tool list.
    if parent_has_explicit:
        parent_tools: Optional[list[str]] = list(parent_strings_only) + [
            f"mcp__{DEFAULT_SERVER_NAME}__{get_meta(c).name}"
            for c in parent_callables
        ]
    else:
        parent_tools = None

    # 5. Rebuild agents with normalized tool lists.
    normalized_agents: dict[str, Any] = {}
    if agents:
        for agent_name, ad in agents.items():
            entries = per_agent_entries.get(agent_name)
            if entries is None:
                # tools=None → leave the AgentDefinition untouched.
                normalized_agents[agent_name] = ad
                continue
            new_tools: list[str] = []
            for entry in entries:
                if isinstance(entry, str):
                    new_tools.append(entry)
                else:
                    meta = get_meta(entry)
                    new_tools.append(
                        f"mcp__{DEFAULT_SERVER_NAME}__{meta.name}"
                    )
            normalized_agents[agent_name] = dataclasses.replace(
                ad, tools=new_tools,
            )

    return all_metas, parent_tools, normalized_agents


# ─────────────────────────────────────────────────────────────────────
# Dispatcher — wraps a tool with validation, audit, event hooks
# ─────────────────────────────────────────────────────────────────────

def _make_dispatcher(
    meta: ToolMeta,
    *,
    run_id: str,
    audit_dir: Path,
    agent_cwd: Path,
    event_bus: EventBus,
    dump_audit: bool,
) -> Callable[[dict], Any]:
    """Build the async dispatcher for one tool. Closure captures run-scope."""
    seq_counter = [0]

    async def dispatch(args: dict) -> dict[str, Any]:
        seq_counter[0] += 1
        call_seq = seq_counter[0]
        t0 = time.monotonic()

        error: Optional[str] = None
        output_serialized: Any = None
        result: dict[str, Any]

        try:
            try:
                parsed = meta.input_model.model_validate(args)
            except Exception as exc:
                raise ValueError(f"input validation failed: {exc}") from exc

            call_args: tuple = (parsed,)
            call_kwargs: dict = {}
            if meta.has_ctx and meta.ctx_param_name:
                ctx = ToolContext(
                    run_id=run_id,
                    agent_cwd=agent_cwd,
                    outputs_dir=agent_cwd / "outputs",
                    attachments_dir=agent_cwd / "attachments",
                    audit_dir=audit_dir,
                )
                call_kwargs[meta.ctx_param_name] = ctx

            if meta.is_async:
                output = await meta.fn(*call_args, **call_kwargs)
            else:
                output = await asyncio.to_thread(
                    meta.fn, *call_args, **call_kwargs,
                )

            if isinstance(output, BaseModel):
                output_serialized = output.model_dump(mode="json")
                text = output.model_dump_json()
            elif isinstance(output, (dict, list)):
                output_serialized = output
                text = json.dumps(output, ensure_ascii=False, default=str)
            else:
                output_serialized = output
                text = str(output)

            result = {"content": [{"type": "text", "text": text}]}

        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            tb = "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__,
            ))
            tb_short = tb if len(tb) < 2000 else tb[:2000] + "\n...[truncated]"
            result = {
                "content": [{
                    "type": "text",
                    "text": f"Tool '{meta.name}' raised {error}\n{tb_short}",
                }],
                "is_error": True,
            }

        duration_ms = int((time.monotonic() - t0) * 1000)

        if dump_audit:
            try:
                audit_dir.mkdir(parents=True, exist_ok=True)
                fname = f"tool_{meta.name}_{call_seq:03d}.json"
                (audit_dir / fname).write_text(
                    json.dumps({
                        "tool": meta.name,
                        "run_id": run_id,
                        "seq": call_seq,
                        "input": args,
                        "output": output_serialized,
                        "error": error,
                        "duration_ms": duration_ms,
                    }, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
            except Exception:
                # Audit write must never break a tool call.
                pass

        try:
            await event_bus.emit_event(Event(
                type="python_tool_call",
                payload={
                    "name": meta.name,
                    "seq": call_seq,
                    "duration_ms": duration_ms,
                    "ok": error is None,
                    "error": error,
                },
                run_id=run_id,
            ))
        except Exception:
            pass

        return result

    return dispatch


# ─────────────────────────────────────────────────────────────────────
# Compile metas → in-process MCP server
# ─────────────────────────────────────────────────────────────────────

def compile_python_tools(
    metas: list[ToolMeta],
    *,
    run_id: str,
    audit_dir: Path,
    agent_cwd: Path,
    event_bus: EventBus,
    server_name: str = DEFAULT_SERVER_NAME,
    server_version: str = "1.0.0",
    dump_audit: bool = True,
) -> tuple[Optional[Any], list[str]]:
    """Compile Python tools into one in-process MCP server.

    Returns:
      (mcp_server_config, prefixed_names) — when metas is empty, returns
      (None, []) so the caller can skip wiring.
    """
    if not metas:
        return None, []

    sdk_tools = []
    prefixed_names = []
    for meta in metas:
        dispatcher = _make_dispatcher(
            meta,
            run_id=run_id,
            audit_dir=audit_dir,
            agent_cwd=agent_cwd,
            event_bus=event_bus,
            dump_audit=dump_audit,
        )
        # The SDK's @tool decorator returns an SdkMcpTool instance ready
        # for create_sdk_mcp_server. We pass the JSON Schema dict from
        # Pydantic — supported per the SDK docstring (line 183).
        sdk_tool = _sdk_tool(
            meta.name, meta.description, meta.input_schema,
        )(dispatcher)
        sdk_tools.append(sdk_tool)
        prefixed_names.append(f"mcp__{server_name}__{meta.name}")

    server = create_sdk_mcp_server(
        name=server_name,
        version=server_version,
        tools=sdk_tools,
    )
    return server, prefixed_names
