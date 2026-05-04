# Core API — `Motor`, `MotorConfig`, `RunTask`, lifecycle

## The 3 objects you'll touch every time

```python
from sophia_motor import Motor, MotorConfig, RunTask
```

| Object | What it is | Where it lives |
|---|---|---|
| `MotorConfig` | All settings (model, workspace, proxy flags, defaults that apply to every run) | constructed once at module top |
| `Motor` | The runtime — owns the proxy, dispatches runs, hosts the event bus | constructed once, reused forever |
| `RunTask` | A single invocation (prompt + per-task overrides) | constructed per call site |

Defaults are designed so the smallest useful program is `Motor()` + `motor.run(RunTask(prompt=...))`.

## Singleton pattern (golden rule #3)

```python
# motor.py — application-wide module
from sophia_motor import Motor, MotorConfig

motor = Motor(MotorConfig(
    default_system="You are a helpful assistant.",
    default_max_turns=10,
))
# No await, no async with. The proxy starts lazily on first .run().
```

```python
# anywhere else
from .motor import motor
from sophia_motor import RunTask

async def handle(query: str) -> str:
    result = await motor.run(RunTask(prompt=query))
    return result.output_text
```

Reuse one motor across N concurrent requests — the proxy multiplexes by `run_id`. Multiple `Motor` instances are only justified for **radically different** configs (different `upstream_base_url`, `workspace_root`, or `guardrail`).

## Lifecycle

| Event | Effect |
|---|---|
| `Motor(config)` | Pure construction — no proxy, no IO. Validates `MotorConfig` (Pydantic) |
| `await motor.run(...)` (1st call) | **Lazy auto-start** of the proxy (~500 ms one-time). Then runs the task |
| `await motor.run(...)` (subsequent) | Reuses proxy. Multi-run parallel via `asyncio.gather` is safe |
| `await motor.stop()` (optional) | Shuts the proxy down. For FastAPI lifespan or explicit cleanup |
| Process exit | Kernel kills uvicorn task, port released. No `motor.stop()` required for scripts that exit cleanly |

`async with Motor(...) as motor: ...` still works for controlled scripts. No breaking changes vs context-managed style.

## `MotorConfig` — what to set, what to leave alone

Every field is documented in the source (`config.py`). The ones you'll touch most:

```python
MotorConfig(
    # Anthropic API
    api_key=...,                       # default: ANTHROPIC_API_KEY env / .env / ""
    model="claude-opus-4-6",           # default; SOPHIA_MOTOR_MODEL env override

    # Proxy / audit
    proxy_dump_payloads=True,          # default False — flip on for dev / audit
    console_log_enabled=True,          # default False — flip on for dev

    # Per-run defaults (override semantics: full replacement, not merge)
    default_system="You are X...",
    default_tools=[Read, my_fn, ...],   # heterogeneous: str + @tool callables
    default_max_turns=20,
    default_output_schema=MyModel,
    default_skills=Path("./skills/"),
    default_attachments=...,
    default_agents={"name": AgentDefinition(...)},

    # Workspace (golden rule #2)
    workspace_root=Path("/data/runs"),  # MUST be outside any repo

    # Security
    guardrail="strict",                 # default; or "permissive" / "off"
)
```

Defaults that flipped recently and bite if you assume otherwise:

| Field | Default in 0.4.3+ | Earlier |
|---|---|---|
| `console_log_enabled` | `False` | `True` |
| `proxy_dump_payloads` | `False` | `True` |

Both are env-overridable: `SOPHIA_MOTOR_CONSOLE_LOG=true`, `SOPHIA_MOTOR_AUDIT_DUMP=true`.

The full env-cascade table:

| Env var | Field | Default value |
|---|---|---|
| `ANTHROPIC_API_KEY` | `api_key` | `""` |
| `SOPHIA_MOTOR_MODEL` | `model` | `"claude-opus-4-6"` |
| `SOPHIA_MOTOR_BASE_URL` | `upstream_base_url` | `"https://api.anthropic.com"` |
| `SOPHIA_MOTOR_ADAPTER` | `upstream_adapter` | `"anthropic"` |
| `SOPHIA_MOTOR_WORKSPACE_ROOT` | `workspace_root` | `~/.sophia-motor/runs` |
| `SOPHIA_MOTOR_PROXY_HOST` | `proxy_host` | `"127.0.0.1"` |
| `SOPHIA_MOTOR_CONSOLE_LOG` | `console_log_enabled` | `False` |
| `SOPHIA_MOTOR_AUDIT_DUMP` | `proxy_dump_payloads` | `False` |

For everything else, the source is the truth: `python -c "import sophia_motor.config as c; print(c.MotorConfig.model_fields)"`.

## `RunTask` — per-call overrides

```python
@dataclass
class RunTask:
    prompt: str
    system: Optional[str] = None
    tools: Optional[list[Any]] = None              # str | callable mix
    allowed_tools: Optional[list[str]] = None      # rarely needed (bypassPermissions)
    disallowed_tools: Optional[list[str]] = None
    max_turns: Optional[int] = None
    attachments: AttachmentsInput = None
    skills: SkillsInput = None
    disallowed_skills: list[str] = []
    agents: Optional[dict[str, Any]] = None        # dict[name, AgentDefinition]
    output_schema: Optional[type[BaseModel]] = None
    session_id: Optional[str] = None               # use Chat instead, usually
    workspace_dir: Optional[Path] = None           # use Chat instead, usually
```

Anything left at the default (`None` / `[]`) falls back to the corresponding `MotorConfig.default_*`. Anything explicitly set (including `[]` for `tools` to mean "no tools at all") wins, **as a full replacement** — never merged.

To **extend** rather than replace:

```python
RunTask(prompt=..., tools=motor.config.default_tools + [extra_tool])
```

## `RunResult` — what comes back

```python
result = await motor.run(task)

result.run_id            # str — run-<unix-ts>-<8-hex>
result.output_text       # Optional[str] — final assistant text
result.output_data       # Optional[BaseModel] — schema-validated, if output_schema set
result.blocks            # list[dict] — every text/thinking/tool_use/tool_result block
result.output_files      # list[OutputFile] — files under <run>/agent_cwd/outputs/
result.metadata          # RunMetadata — turns, tokens, cost, duration, errors
result.audit_dir         # Path — <run>/audit/
result.workspace_dir     # Path — <run>/
```

Pull `output_text` for free-form, `output_data` for structured, `output_files` for generated artefacts. See [`output.md`](output.md) for the full lifecycle (especially the **transient workspace** warning).

`RunMetadata` reference:

| Field | Meaning |
|---|---|
| `run_id` | echoes `result.run_id` |
| `duration_s` | wall-clock seconds for this run |
| `n_turns` | how many turns the agent took |
| `n_tool_calls` | how many tool invocations across the run |
| `input_tokens`, `output_tokens` | cumulative usage |
| `total_cost_usd` | cumulative cost; `0.0` for vLLM (no native billing) |
| `is_error` | `True` if the run failed (network, schema validation, model error). Distinct from `was_interrupted` |
| `error_reason` | human-readable string when `is_error=True` |
| `was_interrupted` | `True` iff `motor.interrupt()` was called for this run; `is_error` stays `False` (deliberate, not a failure) |
| `session_id` | the SDK session_id from the CLI's init message — persist this if you want to resume |

## Typical end-to-end shape

```python
async def assess(query: str) -> Verdict:
    result = await motor.run(RunTask(prompt=query, output_schema=Verdict))
    if result.metadata.is_error:
        raise RuntimeError(result.metadata.error_reason)
    return result.output_data         # already validated
```

## When uncertain

- Field name? `python -c "import sophia_motor.config; help(sophia_motor.config.MotorConfig)"`
- Default value? Read `src/sophia_motor/config.py` directly.
- Behaviour after `run()` ends? Read `motor.py:_run_inner` (`grep -n "_run_inner\|RunResult(" src/sophia_motor/motor.py`).
- Override merge vs replace? Read `motor.py:_apply_config_defaults`.
