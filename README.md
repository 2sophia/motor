# sophia-motor

**Programmable, instanceable agent motor.** Wraps the Claude Agent SDK behind a class-based API with built-in audit, event streaming, and structured-output validation. Built for compliance use cases (banking, regulatory) where every model decision must be traceable and every output schema-strict.

```python
motor = Motor()                                  # pip-installable, zero ceremony

result = await motor.run(RunTask(
    prompt="Valuta l'obbligo X contro i controlli Y, Z.",
    output_schema=Verdict,                       # Pydantic class
    tools=["Read"],
    skills=Path("./my_skills/"),
    max_turns=15,
))

result.output_data        # → instance of Verdict, Pydantic-validated
result.audit_dir          # → <run>/audit/  every request/response dumped for BdI
```

Designed for the pattern *"singleton motor + N small tasks"*: instance the motor once at module top-level, call `motor.run(...)` from any async function, anywhere.

---

## Install

```bash
pip install sophia-motor
```

Requires Python 3.12+. Set `ANTHROPIC_API_KEY` in env or in a local `.env`.

## Quick start

```python
import asyncio
from typing import Literal
from pydantic import BaseModel
from sophia_motor import Motor, MotorConfig, RunTask


class Verdict(BaseModel):
    verdetto: Literal["ALTA", "MEDIA", "BASSA"]
    motivazione: str


# 1) Singleton at module top-level (sync, no await needed)
motor = Motor(MotorConfig(
    default_system="Sei un compliance officer.",
    default_output_schema=Verdict,
    default_max_turns=5,
))


# 2) "Smart functions" are normal Python async defs that build a RunTask
async def assess(obligation: str, controls: list[str]) -> Verdict:
    result = await motor.run(RunTask(
        prompt=(
            f"Valuta se l'obbligo è coperto.\n\n"
            f"OBBLIGO: {obligation}\n\nCONTROLLI:\n"
            + "\n".join(f"- {c}" for c in controls)
        ),
    ))
    return result.output_data


# 3) Use it anywhere — proxy lazy-starts on first call, stays alive
async def main():
    v = await assess(
        obligation="L'organo di controllo verifica entro 30 giorni.",
        controls=["CTRL-001: verifica trimestrale", "CTRL-042: audit annuale"],
    )
    print(f"{v.verdetto} — {v.motivazione}")


asyncio.run(main())
```

See [`examples/verdict_minimal.py`](examples/verdict_minimal.py) for the full pattern.

## Why

Static single-shot LLM calls (one prompt → one JSON) work, but they're not defensible. RGCI's gap-analysis verdict and Sophia's compliance-officer dialogs need:

- **Multi-turn agentic reasoning**: read controls one by one, cross-reference normativa, cite verbatim
- **Strict structured output**: a Pydantic class as output contract, validated by the CLI itself
- **Audit trail**: every request/response persisted for BdI defense
- **Repeatable**: same prompt + cache-friendly system → same cost, same path
- **Reusable**: a Motor instance can serve N tasks of N kinds, wrapped in plain Python functions

`sophia-motor` packages this as a building block: install, instance, call.

## How it works

```
   ┌─────────────────────────────────────────────────────────────┐
   │  Your code  (FastAPI endpoint, batch script, Celery task…)  │
   │     v = await motor.run(RunTask(prompt=..., schema=...))    │
   └─────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  Motor                                                      │
   │   • applies MotorConfig defaults to the task                │
   │   • mints run_id + isolated workspace                       │
   │   • ClaudeSDKClient(subprocess) routes via local proxy      │
   │   • ResultMessage.structured_output → Pydantic validate     │
   │   • emits run_started / tool_use / proxy_request / result   │
   └─────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  ProxyServer  (in-process, kernel-assigned port)            │
   │   • dumps every request_NNN.json + response_NNN.sse         │
   │   • strips SDK billing/identity noise (token savings)       │
   │   • emits proxy_request / proxy_response events             │
   │   • forwards to https://api.anthropic.com                   │
   └─────────────────────────────────────────────────────────────┘
```

Each `motor.run()` produces an isolated workspace:

```
<workspace_root>/run-<ts>-<hex>/
├── input.json                # task params + manifest
├── trace.json                # final blocks + metadata
├── audit/
│   ├── request_001.json      # POST /v1/messages body
│   └── response_001.sse      # streamed response
├── .claude/                  # CLI config (skills + sessions)
│   └── skills/say_hello → /path/to/source (symlink)
└── agent_cwd/                # subprocess sandbox
    ├── attachments/          # input files (links + inline)
    └── outputs/              # files the agent writes
```

## API

### `MotorConfig`

| Field | Default | Description |
|---|---|---|
| `model` | `claude-opus-4-6` | Default model for the SDK |
| `api_key` | from `ANTHROPIC_API_KEY` env or `./.env` | Anthropic API key |
| `workspace_root` | `~/.sophia-motor/runs/` | Run dirs root — **must be outside any repo**, see *Caveats* |
| `proxy_enabled` | `True` | Local proxy for audit dump (don't disable in prod) |
| `disable_claude_md` | `True` | Skip auto-loading repo CLAUDE.md / MEMORY.md |
| `default_system` | `None` | Default system prompt (overridable per task) |
| `default_tools` | `None` | Default hard tool whitelist |
| `default_allowed_tools` | `None` | Default permission-skip list |
| `default_skills` | `None` | Default skills source(s) |
| `default_attachments` | `None` | Default attachments |
| `default_output_schema` | `None` | Default Pydantic class for structured output |
| `default_max_turns` | `20` | Default max agentic turns |
| `default_disallowed_tools` | sensible blocklist | Web access, agent spawn, MCP auth, etc. |

### `RunTask`

Any field set on the task **wins** over the matching `MotorConfig` default.

| Field | Type | Notes |
|---|---|---|
| `prompt` | `str` | Required |
| `system` | `str?` | Static, prompt-cache friendly |
| `tools` | `list[str]?` | Hard whitelist (what the model SEES). `[]` = no tools. `None` = SDK preset |
| `allowed_tools` | `list[str]?` | Permission-skip (auto-run without prompt) |
| `disallowed_tools` | `list[str]?` | Hard block (removed from context) |
| `max_turns` | `int?` | Override default |
| `attachments` | `Path \| dict \| list` | Symlink for paths, inline file for `dict[str, str]` |
| `skills` | `Path \| str \| list` | Multi-source. Each source's subdirs with `SKILL.md` are linked |
| `disallowed_skills` | `list[str]` | Skill names to skip |
| `output_schema` | `type[BaseModel]?` | Pydantic class — CLI validates server-side, motor validates Pydantic-side |

### `RunResult`

| Field | Type | Notes |
|---|---|---|
| `run_id` | `str` | `run-<ts>-<8hex>` |
| `output_text` | `str?` | Final assistant text (free-form) |
| `output_data` | `BaseModel?` | Schema-validated output, present iff `output_schema` was set |
| `blocks` | `list[dict]` | Every text/thinking/tool_use/tool_result block |
| `metadata` | `RunMetadata` | turns, tokens, cost, duration, is_error |
| `audit_dir` | `Path` | `<run>/audit/` |
| `workspace_dir` | `Path` | `<run>/` |

## Skills

Skills are reusable prompts/scripts the model can invoke via the `Skill` tool. Each skill is a directory containing `SKILL.md` (with frontmatter) plus optional scripts.

```python
result = await motor.run(RunTask(
    prompt="...",
    tools=["Skill"],
    skills=[
        Path("./project_skills/"),
        Path("./shared_skills/"),
    ],
    disallowed_skills=["heavy-skill"],
))
```

The motor links each `<source>/<skill_name>/` into `<run>/.claude/skills/<skill_name>/` as a symlink. Conflict between sources (same skill name) raises `ValueError` with the conflicting paths.

## Strict structured output

Pass a Pydantic `BaseModel` class as `output_schema`. The motor:
1. Extracts `model_json_schema()` and forwards via `--json-schema` to the CLI
2. CLI validates server-side (constraints honored: `enum`, `pattern`, `range`, `additionalProperties:false`, nested objects)
3. SDK exposes the validated payload as `ResultMessage.structured_output`
4. Motor calls `OutputSchema.model_validate(...)` → typed instance in `RunResult.output_data`
5. On `ValidationError` → `metadata.is_error = True`, `output_data = None`

The agentic loop (multi-turn tool use) and the structured output **coexist**: the agent reads files, reasons, calls tools, then emits **both** free-form text (`result`) **and** schema-strict JSON (`structured_output`) at the end of the run.

## Events & logging

```python
@motor.on_event
async def on_event(event):
    # event.type ∈ {
    #   "run_started", "system_message", "tool_use", "tool_result",
    #   "assistant_text", "thinking",
    #   "proxy_request", "proxy_response", "result", "sdk_message",
    # }
    pass

@motor.on_log
async def on_log(record):
    # record.level ∈ {"DEBUG", "INFO", "WARNING", "ERROR"}
    pass
```

By default a colored console logger is registered (`console_log_enabled=True`). Disable for silent runs.

## Concurrency model

A single `Motor` handles **one run at a time** (internal `asyncio.Lock`). Multiple concurrent calls to `motor.run()` queue automatically — safe to call from any number of FastAPI endpoints, the proxy and audit dump never race.

For true parallelism, instantiate N motors. Each gets its own kernel-assigned port and audit dir:

```python
m1, m2 = Motor(), Motor()
results = await asyncio.gather(m1.run(task_a), m2.run(task_b))
```

## Caveats

### Workspace must be outside any repo

The bundled Claude CLI binary performs **upward project-root discovery** (`.git/`, `pyproject.toml`, `package.json`). When triggered, it rewrites session/backup state into a deeply-nested cwd-relative fallback, ignoring `CLAUDE_CONFIG_DIR`. Default `workspace_root=~/.sophia-motor/runs/` avoids it.

For containers, mount a volume and set explicitly:
```python
Motor(MotorConfig(workspace_root="/data/sophia-motor/runs"))
```

### Subprocess hardening

The motor disables a dozen CLI behaviors that don't belong in a programmatic agent run (telemetry, title-gen, auto-memory, file checkpointing, ambient CLAUDE.md, terminal title rewrites, git auto-instructions, …). See `CLAUDE.md` → "CLI quirks" section for the full list.

### `--bare` breaks skills

Don't pass `cli_bare_mode=True` if you use skills. In bare mode, skills resolve as slash-commands and the model loses the `Skill` tool. Verified empirically.

## Status

**Pre-alpha** (0.x). API is stable enough to use in `RGCI` and `Sophia`, breaking changes still possible. See [`CLAUDE.md`](CLAUDE.md) for the design doc + roadmap.

## License

MIT.
