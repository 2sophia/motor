# python-tools

Decorate Python functions with `@tool`, pass them to the motor, the model calls them. The motor mounts everything as one in-process MCP server and the agent sees them as `mcp__sophia__<name>`. **No `mcp_servers={...}` wiring, no separate process, no manifest file.**

## Files

- **`main.py`** — single-agent run with three tool flavours
- **`subagent.py`** — same idea, plus subagents that inherit or scope down the toolset

## What `main.py` shows

The three flavours that cover ~95% of real use:

| Tool | Shape | Why |
|---|---|---|
| `fetch_user(args: FetchUserInput) -> FetchUserOutput` | pure data, async | the canonical pattern — Pydantic in, Pydantic out, no filesystem |
| `write_report(args, ctx: ToolContext) -> ReportOutput` | uses `ToolContext` | when the tool needs run-scoped paths (`ctx.outputs_dir`, `ctx.run_id`, `ctx.attachments_dir`) — anything written under `ctx.outputs_dir` surfaces in `RunResult.output_files` |
| `hash_payload(args: HashInput) -> HashOutput` | sync `def` | sync functions are auto-wrapped in `asyncio.to_thread`. Use this for CPU-bound or sync-only libraries (hashlib, pdf parsers, bcrypt, ...) |

The prompt asks the model to do all three in sequence, so the run exercises every flavour:

```python
default_tools=[fetch_user, write_report, hash_payload]
# That's the whole wiring.
```

## What `subagent.py` adds

How the same `@tool` callables flow through subagent dispatches. Two patterns side-by-side in the same run:

- **Pattern A — inheritance**: `AgentDefinition(...)` with **no `tools=` argument** → the subagent inherits the parent's full toolset (`fetch_user`, `hash_payload`, ...). Verified empirically: SDK does the work, motor rides along.
- **Pattern B — explicit restrict**: `AgentDefinition(..., tools=[write_report])` → the subagent sees ONLY `write_report`, even if the parent has more. The motor still mounts every callable referenced anywhere in the run on a single shared MCP server (deduped by name), and rewrites each subagent's `tools` to the prefixed `mcp__sophia__<name>` form.

`write_report` lives **only** inside the writer subagent's `tools` — not on the parent — and the motor still finds it and exposes it correctly. That's the proof that the collection / dedup logic works.

## Run

```bash
pip install sophia-motor
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python main.py        # ~$0.04, 4-6 turns, 3 tool calls
python subagent.py    # ~$0.10, 6-8 turns, 4-5 tool calls (parent + subagents)
```

Both scripts print `[tool] ✓ <name> (<ms>)` lines as the agent calls them, then dump per-call audit JSONs under `<run>/audit/tool_<name>_<seq>.json` (when `proxy_dump_payloads=True`, on in these examples for transparency).

## What you get for free

- **Pydantic schema derivation** — the model sees a typed JSON Schema built from your input class. Wrong types raise before the call.
- **Audit dump per call** — every invocation's input + output + duration + error gets persisted (when audit is on).
- **Live event stream** — subscribe with `@motor.on_event`; `python_tool_call` events fire as soon as a tool returns. The example wires this up to the `[tool]` log line.
- **No manifest, no mcp config files** — pass the function objects directly. Strings and callables can mix in the same `default_tools` list (`["Read", fetch_user, "Bash"]` is valid).

## Adding `ctx: ToolContext`

If you add `ctx: ToolContext` as the **second parameter**, the motor injects it automatically — no annotation forwarding, no manual binding. Inside the body you can read:

| Attribute | What |
|---|---|
| `ctx.run_id` | unique id for this run |
| `ctx.agent_cwd` | the agent's sandboxed cwd (where `Read` / `Glob` resolve) |
| `ctx.outputs_dir` | `<run>/agent_cwd/outputs/` — anything you write here is surfaced as an `OutputFile` |
| `ctx.attachments_dir` | `<run>/agent_cwd/attachments/` — read-only seeded files |
| `ctx.audit_dir` | `<run>/audit/` — same dir the proxy dumps to |

`ctx` is **opt-in** by signature. A tool without `ctx` works identically; the motor checks via `inspect.signature` and only injects when present.

## See also

- [`MotorConfig.default_tools`](../../src/sophia_motor/config.py) — the heterogeneous list semantics (str | callable mix)
- [`ToolContext`](../../src/sophia_motor/_python_tools.py) — full attribute reference
- The [`subagents/`](../subagents/) sibling for the standalone subagent patterns (no `@tool`, just SDK built-in tools)
