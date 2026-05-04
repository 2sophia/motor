# Python tools — `@tool` decorator + `ToolContext`

The headline feature of v0.5.0. Decorate a Python function with `@tool`, pass it to `MotorConfig.default_tools` next to the built-in tool name strings. The agent invokes it like anything else — schema, validation, audit dump are derived from your Pydantic types. No `mcp_servers={...}` wiring, no `allowed_tools=["mcp__server__tool", ...]` lists.

```python
from sophia_motor import tool       # also `Motor.tool` alias for namespaced use
```

## Required shape

```python
from pydantic import BaseModel
from sophia_motor import tool

class GreetInput(BaseModel):
    name: str

class GreetOutput(BaseModel):
    greeting: str

@tool
async def greet(args: GreetInput) -> GreetOutput:
    """Greet the user by name. Use whenever a salutation is needed."""
    return GreetOutput(greeting=f"Hello, {args.name}!")
```

Hard requirements (validated at `Motor()` construction → `RuntimeError` if violated):

1. **First positional parameter** type-annotated as a Pydantic `BaseModel` subclass. This is the schema source — `args.model_json_schema()` is what the model sees.
2. **Description present** — the docstring or `@tool(description=...)`. The motor refuses tools with empty description.
3. **Decorator present** — bare `async def` is *not* a tool.

The function name is what the model sees (rewritten to `mcp__sophia__<name>` per MCP convention — see naming below). Override with `@tool(name="custom.name")` if needed.

## Forms accepted

```python
@tool                                      # bare — name from __name__, desc from __doc__
async def my_tool(args: Inp) -> Out: ...

@tool(name="users.fetch")                  # override name
async def my_tool(args: Inp) -> Out: ...

@tool(description="What the model reads")  # override description (docstring stays internal)
async def my_tool(args: Inp) -> Out: ...

@tool(examples=[                            # appended to description, lifts hit rate
    {"input": {"id": 1}, "output": {"name": "Alex"}},
    {"input": {"id": 99}, "output": {"name": "Bob"}},
])
async def fetch(args: Inp) -> Out: ...
```

`Motor.tool` is a static-method alias — useful when the dev imports `Motor` already:

```python
from sophia_motor import Motor

@Motor.tool
async def my_tool(args: Inp) -> Out: ...
```

Both forms attach the same metadata. Use whichever reads better.

## Sync functions are accepted

```python
@tool
def hash_payload(args: HashInput) -> HashOutput:
    """SHA-256 a string — synchronous I/O is fine for fast deterministic ops."""
    import hashlib
    return HashOutput(sha256=hashlib.sha256(args.data.encode()).hexdigest())
```

Sync functions are auto-wrapped via `asyncio.to_thread`. Fine for I/O-bound work; for >100 ms CPU prefer async to avoid blocking the event loop.

## Mounting them

Pass directly into `default_tools` / `RunTask.tools` — same list as built-in tool name strings:

```python
motor = Motor(MotorConfig(
    default_tools=["Read", "Glob", greet, hash_payload],
))
```

The motor:
- splits the list into strings (built-ins) and callables (Python tools)
- validates callables (decorator presence, no name collisions, no built-in shadowing)
- mounts them on a single in-process MCP server named `sophia`
- exposes them to the agent as `mcp__sophia__<name>`

The model's view of the tools list is `["Read", "Glob", "mcp__sophia__greet", "mcp__sophia__hash_payload"]`. The prefix is intentional (standard MCP convention; debug-friendly in audit dumps; we don't rewrite).

## `ToolContext` — opt-in run-scoped paths

When your tool needs to write into the run's workspace or read attachments at run-resolved paths, declare a parameter annotated `ToolContext`:

```python
from sophia_motor import ToolContext

class ReportInput(BaseModel):
    subject: str
    body: str

@tool
async def write_report(args: ReportInput, ctx: ToolContext) -> str:
    """Persist a report under <run>/agent_cwd/outputs/."""
    ctx.outputs_dir.mkdir(parents=True, exist_ok=True)
    p = ctx.outputs_dir / f"{args.subject}.md"
    p.write_text(args.body)
    return str(p)
```

Detection is by **type annotation**, not name — call it `ctx`, `context`, `run_ctx`, whatever. The motor uses `inspect.signature` + `typing.get_type_hints` at decoration time.

`ToolContext` fields (all `Path` except `run_id`):

| Field | What it points at |
|---|---|
| `run_id` | `str` — `run-<unix-ts>-<8-hex>` |
| `agent_cwd` | `<workspace_root>/<run_id>/agent_cwd/` |
| `outputs_dir` | `<workspace_root>/<run_id>/agent_cwd/outputs/` (where `result.output_files` collects from) |
| `attachments_dir` | `<workspace_root>/<run_id>/agent_cwd/attachments/` |
| `audit_dir` | `<workspace_root>/<run_id>/audit/` |

Files written under `ctx.outputs_dir` automatically surface in `result.output_files` at run end. Files written under `ctx.audit_dir` are visible in the per-run audit trail (use sparingly — for tools that produce structured debug data the user may want to inspect).

**No `motor` back-reference.** Tools cannot recursively invoke `motor.run(...)` from inside themselves — that's a deferred decision (would need recursion guards). For tool-to-tool composition, structure your code so the model orchestrates the calls.

## What you get for free, per `@tool` invocation

1. **Input validation** — `args` is `model_validate`d before your function sees it. Bad input → `is_error=True` returned to the model with a clear "input validation failed: ..." message; your function isn't called.
2. **Output serialization** — return a Pydantic instance, dict, list, or primitive; the motor JSON-encodes correctly.
3. **Per-call audit dump** — `<run>/audit/tool_<name>_<seq>.json` containing `{tool, run_id, seq, input, output, error, duration_ms}`. Sequenced (001, 002, ...) so multiple invocations of the same tool are distinguishable. Honors `MotorConfig.proxy_dump_payloads` (default off in production).
4. **Event emission** — `Event(type="python_tool_call", payload={name, seq, duration_ms, ok, error})` on the bus, every call. Use for live UI / metrics:

```python
@motor.on_event
def watch(ev):
    if ev.type == "python_tool_call":
        ok = "✓" if ev.payload["ok"] else "✗"
        print(f"  [tool] {ok} {ev.payload['name']} ({ev.payload['duration_ms']}ms)")
```

5. **Exception → tool_result(is_error=True)** — never crashes the run. The traceback (truncated to 2KB) goes to the model so it can react.

## Where `@tool` callables can live

| Place | Effect |
|---|---|
| `MotorConfig(default_tools=[my_tool])` | applied to every run on this motor |
| `RunTask(tools=[my_tool])` | overrides default for this run only |
| `AgentDefinition(tools=[my_tool])` | restrict a subagent to a subset (see [`subagents.md`](subagents.md)) |

The motor collects every callable referenced anywhere in the run, dedupes by name, and mounts them on **one shared** in-process MCP server. So:

- **Same callable in parent + agent** → registered once, both see it
- **Different callables with same `meta.name`** → `ValueError` at run construction
- **Callable only in `AgentDefinition.tools`** → still mounted; only the subagent can invoke it

## Validation errors at config time (fail-fast)

```python
@tool
async def bad(args, ctx):                       # ❌ no annotation
    ...
# TypeError: @tool function 'bad' first parameter must be type-annotated...

@tool
async def bad(args: dict) -> str:               # ❌ first param not Pydantic
    ...
# TypeError: ...first parameter must be a Pydantic BaseModel subclass...

@tool                                           # ❌ no docstring AND no description
async def bad(args: Inp) -> Out:
    return Out()
# ValueError: @tool function 'bad' must have a description...

@tool(name="Read")                              # ❌ collides with built-in
async def my_read(args: Inp) -> Out: ...
# ValueError: @tool 'Read' collides with a built-in tool name. Rename via @tool(name='...')
```

These all surface at `Motor()` construction, not at `motor.run()` time. Iterate fast.

## When to NOT use `@tool`

- **Pure reasoning task** (no I/O the model needs) → just send the prompt; tools are noise that costs tokens.
- **Live filesystem exploration** → use built-in `Read` / `Glob` / `Grep`. They're optimized for that.
- **Web fetching** → built-in `WebFetch` is more flexible than wrapping `httpx` yourself.
- **Tool that needs to call another tool** → restructure: let the model invoke them in sequence (the model is good at chaining tools across turns).

## Live verification

`examples/python-tools/main.py` (basic) and `examples/python-tools/subagent.py` (parent + 2 subagents with mixed tool sets) — both end-to-end runs at $0.04 / $0.10. Read those before writing your own; they show the canonical patterns.

## When uncertain

- Decorator semantics? Read `src/sophia_motor/_python_tools.py` — the file is ~400 lines, fully readable.
- ToolContext fields? `python -c "from sophia_motor import ToolContext; help(ToolContext)"`.
- "How does it know my function is sync vs async?" → `inspect.iscoroutinefunction(fn)` at decoration time, stored on `ToolMeta.is_async`.
- "What if I import the wrong `tool`?" → there's a `tool` symbol in `claude_agent_sdk` too. Make sure you `from sophia_motor import tool`. If you mix them up, you'll get an `SdkMcpTool` object instead of a decorated function — `isinstance(my_tool, types.FunctionType)` is the smoke test.
