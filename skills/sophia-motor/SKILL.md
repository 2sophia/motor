---
name: sophia-motor
description: Use this skill whenever the developer wants to build a Python agent that can call tools, write structured output, fetch from a DB, persist files, or run multi-turn conversations on top of Anthropic's Claude API (or a vLLM-served Qwen). `sophia-motor` is a pip-installable wrapper around the Claude Agent SDK that adds an HTTP audit proxy, a `@tool` decorator that exposes Python functions to the model with Pydantic schemas, in-process MCP mounting, per-turn event bus, isolated subagents, and a singleton-friendly lifecycle. Reach for this skill instead of writing raw `claude_agent_sdk` boilerplate when the user wants production-grade orchestration with audit dump, structured output, retry semantics, and clean ergonomics.
---

# sophia-motor

A Python motor that turns Anthropic's Claude into a programmable, auditable agent. One `Motor` instance, N concurrent `RunTask` invocations, every turn dumped to disk, custom Python functions exposed to the model via a single `@tool` decorator. `pip install sophia-motor`.

This skill teaches you how to use the library *correctly* — defaults, gotchas, contracts, the patterns the maintainers shipped to production. Read the rules below, scan the decision tree, then progressively disclose a sub-doc when you hit the matching topic. Do **not** try to keep all of this in your head at once.

---

## 🥇 Golden rules (read every time)

These are the non-negotiable defaults. Skipping them creates bugs that look like motor bugs but are actually you fighting the library.

### 1. When uncertain, read the installed source — never guess

The motor is pre-1.0 (`sophia-motor` minor versions can ship breaking changes inside the same major). If you're about to write `MotorConfig(...)` and you're not 100% sure of a field name, default value, or signature, **stop and inspect the actual installed package** before typing. Two-line check:

```bash
python -c "import sophia_motor; print(sophia_motor.__file__)"
# → /path/to/.venv/lib/python3.12/site-packages/sophia_motor/__init__.py
# Then list:
ls $(python -c "import sophia_motor, os; print(os.path.dirname(sophia_motor.__file__))")
```

Then `cat` or `grep` the file you need:
- `config.py` — every `MotorConfig` field, default, and env-var resolution cascade
- `_models.py` — `RunTask`, `RunResult`, `RunMetadata`, `OutputFile`
- `_python_tools.py` — `@tool` decorator semantics, `ToolContext`
- `_chat.py` — `Chat` class
- `motor.py` — `Motor` methods, `_build_sdk_options` (where SDK config is assembled)

This habit saves debugging time on:
- Defaults that flipped between minor versions (e.g. `console_log_enabled` and `proxy_dump_payloads` flipped to `False` in 0.4.3 — older blog posts assume `True`)
- Field names that get renamed
- Behaviour that the README hints at but only the source spells out (e.g. tool-vs-disallowed conflict resolution)

If the user is on a different version than this skill assumes, the source is the authority — not the skill.

### 2. Workspace must be **outside any repo**

`MotorConfig.workspace_root` defaults to `~/.sophia-motor/runs/`. **Never** point it at a folder whose ancestors contain `.git/`, `pyproject.toml`, or `package.json`. The bundled Claude CLI does upward "project root discovery" and, if triggered, re-paths its session/backup state into a deeply-nested fallback location — your runs end up scattered. No env var (including `CLAUDE_PROJECT_DIR`) overrides this. In Docker: `MotorConfig(workspace_root="/data/runs")` with `/data` mounted.

### 3. `Motor()` is a top-level singleton, not a per-request object

```python
# motor.py at module top
motor = Motor(MotorConfig(...))   # sync; no await, no async with

# anywhere else
result = await motor.run(RunTask(prompt="..."))
```

The proxy boots **lazily** at the first `motor.run()`. Reuse the same `motor` for every request — N concurrent runs are isolated by `run_id` in the proxy registry, no lock. Multiple `Motor` instances are only justified when you genuinely need different `MotorConfig` (different upstream, different workspace, different guardrail). For plain concurrency: one motor.

### 4. Tools default to `[]` — least privilege

`MotorConfig.default_tools=[]` out of the box. The model sees **zero** tools unless you opt in (`default_tools=["Read", "Glob"]`, or pass `@tool`-decorated callables). This is by design — a fresh `Motor()` is pure reasoning, no actions. To restore the SDK preset (every built-in), set `default_tools=None` (advanced).

### 5. The proxy is load-bearing, not debug

`MotorConfig.proxy_enabled=True` by default. The proxy is the only point of audit dump, per-turn events, and SDK-noise stripping. Disable it only inside unit tests that mock the SDK. If you see a network glitch in the log, that's the proxy mapping it to a 502 / SSE error event — the SDK retries above it.

### 6. `@tool` decorator is mandatory on Python functions

A bare `async def` is **not** a tool. The motor refuses callables without the decorator at run construction time:

```python
from sophia_motor import tool        # also `Motor.tool` alias

@tool
async def my_fn(args: MyInput) -> MyOutput:
    """One-line description the model reads in the tool catalogue."""
    ...
```

The first positional must be a Pydantic `BaseModel` subclass (the schema source). The docstring or `@tool(description="...")` is what the model sees. See [`python-tools.md`](python-tools.md).

### 7. Override semantics: full replacement, never merge

When `RunTask` sets a field that has a `MotorConfig.default_*` counterpart, it **replaces** the default — does not merge. To extend, build the union manually:

```python
RunTask(prompt="...", tools=motor.config.default_tools + [extra_fn])
```

This is uniform: `tools`, `allowed_tools`, `disallowed_tools`, `disallowed_skills`, `agents`, `attachments`, `skills`, `output_schema`, `system`, `max_turns`. No exceptions.

### 8. Don't bake `ANTHROPIC_API_KEY` into code

Resolution cascade (process env → `./.env` → empty). The motor raises a clear error at first `.run()` if all three are missing. In Docker, pass `-e ANTHROPIC_API_KEY=...`; never bake into the image.

### 9. Italian-informal in chat, English in code

The maintainers (`Sophia AI`) speak Italian in commit messages and chat; the codebase, docstrings, README, and skills are English. Match the convention.

---

## 🚦 Quickstart — three lines and a prompt

```python
import asyncio
from sophia_motor import Motor, RunTask

motor = Motor()    # reads ANTHROPIC_API_KEY from env or ./.env

async def main():
    result = await motor.run(RunTask(prompt="What is 2 + 2?"))
    print(result.output_text)
    print(f"cost: ${result.metadata.total_cost_usd:.4f}")

asyncio.run(main())
```

Cost: a few tenths of a cent. No tools, no workspace files, just reasoning. From here you add tools, schemas, attachments, subagents, streaming.

---

## 🌳 Decision tree — which sub-doc to load

| The user wants to… | Load | Quick verb |
|---|---|---|
| Install or set up `.env` | [`installation.md`](installation.md) | `pip install sophia-motor` |
| Configure model / API key / workspace / proxy | [`core-api.md`](core-api.md) | `MotorConfig(...)` |
| Write a one-shot prompt → answer | [`core-api.md`](core-api.md) | `motor.run(RunTask(prompt=...))` |
| Get a Pydantic-typed result instead of free text | [`output.md`](output.md) | `output_schema=MyModel` |
| Give the agent built-in CLI tools (Read, Glob, Bash…) | [`tools-builtin.md`](tools-builtin.md) | `tools=["Read","Glob"]` |
| **Expose a Python function the model can call** | [`python-tools.md`](python-tools.md) | `@tool` + Pydantic |
| Drop in `SKILL.md` task instructions | [`skills.md`](skills.md) | `skills=Path("./skills")` |
| Hand the agent a folder of files to read | [`attachments.md`](attachments.md) | `attachments=Path("./files")` |
| Persist files the agent writes outside the run dir | [`output.md`](output.md) | `result.output_files[i].copy_to(...)` |
| Render output token-by-token in a UI | [`streaming.md`](streaming.md) | `async for chunk in motor.stream(task)` |
| Multi-turn dialog with memory | [`multi-turn.md`](multi-turn.md) | `motor.chat()` |
| Cancel a running task | [`multi-turn.md`](multi-turn.md) | `motor.interrupt()` |
| Spawn specialist subagents | [`subagents.md`](subagents.md) | `default_agents={...}` + `"Agent"` in tools |
| Hook into every turn (logs, telemetry, UI) | [`observability.md`](observability.md) | `@motor.on_event` |
| Run against vLLM / Qwen / OpenAI | [`adapters.md`](adapters.md) | `SOPHIA_MOTOR_BASE_URL`, `VLLMAdapter` |
| Tighten or loosen the security guardrail | [`security.md`](security.md) | `guardrail="strict"` |
| Build a chat backend with N users | [`patterns.md`](patterns.md) | one motor + per-user `Chat` |
| Find a field, default, env var, event type | [`reference.md`](reference.md) | tables for everything |
| Diagnose "it doesn't work like I expected" | [`troubleshooting.md`](troubleshooting.md) | the 10 known gotchas |

---

## 📦 What this skill assumes you can find

The motor expects this layout in the user's project:

```
project/
├── .env                       # ANTHROPIC_API_KEY=sk-ant-...
├── motor.py                   # `motor = Motor(MotorConfig(...))` at module level
├── ...                        # rest of the application
└── (transient, auto-managed)
    ~/.sophia-motor/runs/       # per-run workspace + audit (outside repo)
```

The user does **not** need to set up Docker, daemons, or external MCP processes. Pip install, write `motor.py`, call from anywhere.

---

## 🚫 What this skill is NOT for

- **Writing a `SKILL.md` to be invoked by the agent itself.** That's the SDK skills feature — load [`skills.md`](skills.md). This file you're reading is a Claude Code skill (helps a *developer's* Claude write motor code), not an agent skill.
- **Replacing the raw Claude Agent SDK for one-off scripts that don't need audit, structured output, multi-turn, or custom tools.** The motor adds value where orchestration matters; for `client.query("hello")` the SDK is fine.
- **OpenAI / Gemini natively.** The proxy + adapter plumbing supports it (subclass `UpstreamAdapter`), but no shipped adapter — body re-mapping is non-trivial. See [`adapters.md`](adapters.md) "shipping a custom adapter".

---

## 🧠 How to use this skill in practice

1. **Recognize the trigger.** User mentions: agent, Claude API, tool calling, MCP, structured output, multi-turn, audit, RAG with custom retrieval, "I want my Python function callable by Claude". → Open SKILL.md.
2. **Read the golden rules.** Always.
3. **Find the closest decision-tree row.** Open that sub-doc. Each sub-doc is self-contained — you don't need to load the others to act.
4. **Before writing code, verify against the installed source.** `python -c "import sophia_motor as m; print(m.__file__)"`. Read the relevant file. Defaults shift between minor versions; the skill assumes a recent 0.5.x — if older, check.
5. **Write the smallest thing that compiles.** The motor's defaults are designed to give a working run with `Motor()` + `motor.run(RunTask(prompt=...))`. Add complexity only when needed.
6. **Run, then iterate from the audit dump.** Every run lands in `<workspace_root>/<run_id>/audit/` with the full `/v1/messages` exchange. When something looks weird, `cat <run>/audit/request_001.json` is faster than guessing.

---

## ✏️ One worked example end-to-end

This is the canonical "do something useful" pattern — covers config, custom tool, structured output, persistence, audit. If the user shows you something simpler, scale down. If they show you something more complex, layer on (tools list / subagents / chat).

```python
import asyncio
from pathlib import Path
from pydantic import BaseModel
from sophia_motor import Motor, MotorConfig, RunTask, ToolContext, tool


# ── Domain types ─────────────────────────────────────────────────────
class CustomerLookup(BaseModel):
    customer_id: str

class Customer(BaseModel):
    name: str
    tier: str
    monthly_revenue_usd: float


# ── Custom tool — the model invokes this Python fn ───────────────────
_DB = {
    "ACME-001": ("Acme Corp", "enterprise", 24_500),
    "BETA-002": ("Beta GmbH", "startup",     1_200),
}

@tool
async def fetch_customer(args: CustomerLookup) -> Customer:
    """Look up a customer by internal ID. Returns tier and current MRR."""
    name, tier, mrr = _DB[args.customer_id]
    return Customer(name=name, tier=tier, monthly_revenue_usd=mrr)


# ── Custom tool that uses ToolContext to write into the run workspace
class BriefingInput(BaseModel):
    customer_id: str
    body: str

@tool
async def save_briefing(args: BriefingInput, ctx: ToolContext) -> str:
    """Persist a briefing markdown file under the run's outputs/ folder."""
    p = ctx.outputs_dir / f"briefing_{args.customer_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args.body)
    return str(p)


# ── Structured final output ──────────────────────────────────────────
class Verdict(BaseModel):
    winner: str
    rationale: str
    saved_to: str


# ── Singleton motor ──────────────────────────────────────────────────
motor = Motor(MotorConfig(
    default_tools=[fetch_customer, save_briefing],
    default_output_schema=Verdict,
))


async def main():
    result = await motor.run(RunTask(prompt=(
        "Compare ACME-001 and BETA-002 on MRR. "
        "Save a one-paragraph briefing for the bigger account, "
        "then return the structured verdict."
    )))

    v: Verdict = result.output_data        # typed, validated
    print(v.model_dump_json(indent=2))

    # The agent wrote a file; persist it outside the transient workspace.
    for f in result.output_files:
        f.copy_to(Path("./output"))

    # Audit defense
    print(f"audit: {result.audit_dir}")
    print(f"cost:  ${result.metadata.total_cost_usd:.4f}")


asyncio.run(main())
```

What this exercises:
- Singleton motor with two `@tool` callables and a default output schema (golden rule #3, #6).
- `ToolContext` injection for `save_briefing` — the motor populates run paths so the tool persists into `<run>/agent_cwd/outputs/`.
- `RunResult.output_data` (Pydantic-typed) + `RunResult.output_files` (persisted with `copy_to`).
- Audit trail at `result.audit_dir` covers every `/v1/messages` exchange and every `@tool` invocation (`tool_*.json`).

Sub-docs to load if you go deeper from here:
- [`python-tools.md`](python-tools.md) — full `@tool` semantics, sync vs async, examples in description, `ToolContext` fields
- [`output.md`](output.md) — Pydantic schema rules + `output_files` lifecycle
- [`subagents.md`](subagents.md) — when to split into specialist agents

---

## 📚 Sub-docs index

- [`installation.md`](installation.md) — pip + `.env` + first run + Python version (3.12+)
- [`core-api.md`](core-api.md) — `Motor`, `MotorConfig`, `RunTask`, lifecycle, env-var cascade
- [`tools-builtin.md`](tools-builtin.md) — Read/Edit/Write/Glob/Grep/Bash/WebSearch/WebFetch with their guardrail interactions
- [`python-tools.md`](python-tools.md) — `@tool` + `ToolContext` + sync wrap + signature rules
- [`skills.md`](skills.md) — `SKILL.md` mounting, multi-source folders, name conflicts
- [`attachments.md`](attachments.md) — `Path` / `dict` / mixed lists, hard-link vs symlink, why
- [`output.md`](output.md) — `output_text` / `output_data` / `output_files`, transient workspace, `copy_to`
- [`streaming.md`](streaming.md) — `motor.stream()`, every chunk type, ordering quirks
- [`multi-turn.md`](multi-turn.md) — `Chat`, `Console`, `motor.interrupt()`, session resume
- [`subagents.md`](subagents.md) — `AgentDefinition`, inheritance vs explicit-restrict, opt-in dance
- [`adapters.md`](adapters.md) — `AnthropicAdapter`, `VLLMAdapter`, custom subclasses, env swap
- [`security.md`](security.md) — `guardrail` modes, sandbox, what each tool can & can't do
- [`observability.md`](observability.md) — `EventBus`, every `Event.type`, `LogRecord`, default subscribers
- [`patterns.md`](patterns.md) — singleton, concurrency, chat backend, RGCI verdict shape
- [`reference.md`](reference.md) — full tables: MotorConfig fields, RunTask fields, env vars, event types
- [`troubleshooting.md`](troubleshooting.md) — the 10 known gotchas + how to recognize them in logs

---

*Skill version: 0.1, built against `sophia-motor==0.5.0`. When the installed version differs, trust the source over this skill (golden rule #1).*
