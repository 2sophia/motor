# Reference — fields, env vars, event types, exports

The ground truth is the installed source. This is a stale-by-design quick lookup. **Always check the source for your version** (golden rule #1):

```bash
python -c "import sophia_motor, os; print(os.path.dirname(sophia_motor.__file__))"
```

Built against `sophia-motor==0.5.0`.

## Public exports — what's importable from `sophia_motor`

```python
from sophia_motor import (
    # Core
    Motor, MotorConfig, RunTask, RunResult, RunMetadata,
    OutputFile, clean_runs,

    # Events / logging
    Event, EventBus, LogRecord,
    default_console_logger, default_console_event_logger,

    # Streaming chunks (discriminated union)
    StreamChunk,
    RunStartedChunk, InitChunk,
    TextDeltaChunk, TextBlockChunk,
    ThinkingDeltaChunk, ThinkingBlockChunk,
    ToolUseStartChunk, ToolUseDeltaChunk,
    ToolUseCompleteChunk, ToolUseFinalizedChunk,
    ToolResultChunk, OutputFileReadyChunk,
    ErrorChunk, DoneChunk,

    # Provider adapters
    UpstreamAdapter, AnthropicAdapter, VLLMAdapter,

    # Multi-turn
    Chat,

    # Subagents (re-exported from claude-agent-sdk)
    AgentDefinition,

    # Python tools
    tool, ToolContext, ToolMeta,
)
```

## `MotorConfig` fields

| Field | Default | Env-var override | Purpose |
|---|---|---|---|
| `api_key` | `""` (empty) | `ANTHROPIC_API_KEY` | Anthropic API key — also reads from `./.env` file |
| `model` | `"claude-opus-4-6"` | `SOPHIA_MOTOR_MODEL` | Default model used by SDK + forwarded upstream |
| `upstream_base_url` | `"https://api.anthropic.com"` | `SOPHIA_MOTOR_BASE_URL` | Where the proxy POSTs |
| `upstream_adapter` | `"anthropic"` | `SOPHIA_MOTOR_ADAPTER` | Preset name OR `UpstreamAdapter` instance |
| `anthropic_version` | `"2023-06-01"` | — | `anthropic-version` header forwarded upstream |
| `workspace_root` | `~/.sophia-motor/runs` | `SOPHIA_MOTOR_WORKSPACE_ROOT` | Per-run workspace root. **Outside any repo** |
| `proxy_enabled` | `True` | — | Local proxy for audit + events. Don't disable in prod |
| `proxy_host` | `"127.0.0.1"` | `SOPHIA_MOTOR_PROXY_HOST` | Proxy bind host |
| `proxy_port` | `None` | — | `None` = kernel-assigned (recommended). Set int to pin |
| `proxy_dump_payloads` | `False` (since 0.4.3) | `SOPHIA_MOTOR_AUDIT_DUMP` | Persist `<run>/audit/` dumps |
| `proxy_strip_sdk_noise` | `True` | — | Strip SDK billing/identity blocks from system field |
| `proxy_strip_user_system_reminders` | `True` | — | Strip CLI-injected `<system-reminder>` from user msgs (preserves skill catalogue) |
| `tool_description_overrides` | `{"Read": "..."}` | — | Map `tool_name → description` rewritten by proxy |
| `guardrail` | `"strict"` | — | Built-in `PreToolUse` hook mode: `strict` / `permissive` / `off` |
| `disable_claude_md` | `True` | — | Sets `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` so CLI ignores CLAUDE.md |
| `console_log_enabled` | `False` (since 0.4.3) | `SOPHIA_MOTOR_CONSOLE_LOG` | Pre-register colored console event/log subscribers |
| `cli_bare_mode` | `False` | — | Pass `--bare` to CLI. **Don't enable** if using skills |
| `cli_no_session_persistence` | `True` | — | Pass `--no-session-persistence` (skipped in chat-mode) |
| `default_system` | `None` | — | Per-run default system prompt |
| `default_tools` | `[]` | — | Per-run default tools list (str + callable mix). `None` = SDK preset |
| `default_allowed_tools` | `None` | — | Per-run default permission-skip (rarely needed) |
| `default_skills` | `None` | — | Per-run default skills folder(s) |
| `default_attachments` | `None` | — | Per-run default attachments |
| `default_disallowed_skills` | `[]` | — | Per-run default skill blocklist |
| `default_disallowed_tools` | `DEFAULT_DISALLOWED_TOOLS` (see below) | — | Per-run default tool blocklist |
| `default_agents` | `{}` | — | Per-run default subagents (dict[name, AgentDefinition]) |
| `default_max_turns` | `20` | — | Per-run default max turns |
| `default_timeout_seconds` | `300` | — | Reserved (not yet wired in `run()`) |
| `default_output_schema` | `None` | — | Per-run default Pydantic output class |

Resolution cascade for env-overridable fields: **explicit kwarg > env var > `./.env` file > hardcoded default**.

## `DEFAULT_DISALLOWED_TOOLS`

The list of tools blocked by default. Whitelisting any of these in `tools=[...]` automatically removes it from disallowed for that run (conflict resolution).

```
WebFetch, WebSearch,
AskUserQuestion, TodoWrite, Agent,
EnterPlanMode, ExitPlanMode,
TaskOutput, TaskStop,
EnterWorktree, ExitWorktree,
CronCreate, CronDelete, CronList,
Monitor, PushNotification, ScheduleWakeup,
NotebookEdit, RemoteTrigger,
mcp__claude_ai_Gmail__authenticate (+ complete),
mcp__claude_ai_Google_Calendar__authenticate (+ complete),
mcp__claude_ai_Google_Drive__authenticate (+ complete),
```

## `RunTask` fields

| Field | Type | Default |
|---|---|---|
| `prompt` | `str` | required |
| `system` | `Optional[str]` | `None` |
| `tools` | `Optional[list[Any]]` (str + Callable mix) | `None` (→ `default_tools`) |
| `allowed_tools` | `Optional[list[str]]` | `None` |
| `disallowed_tools` | `Optional[list[str]]` | `None` |
| `max_turns` | `Optional[int]` | `None` |
| `attachments` | `Path \| str \| dict \| list[...]` | `None` |
| `skills` | `Path \| str \| list[...]` | `None` |
| `disallowed_skills` | `list[str]` | `[]` |
| `agents` | `Optional[dict[str, AgentDefinition]]` | `None` |
| `output_schema` | `Optional[type[BaseModel]]` | `None` |
| `session_id` | `Optional[str]` | `None` (use `Chat`) |
| `workspace_dir` | `Optional[Path]` | `None` (use `Chat`) |

Override semantics: full replacement, never merge.

## `RunResult` fields

| Field | Type |
|---|---|
| `run_id` | `str` |
| `output_text` | `Optional[str]` |
| `output_data` | `Optional[BaseModel]` |
| `blocks` | `list[dict]` |
| `output_files` | `list[OutputFile]` |
| `metadata` | `RunMetadata` |
| `audit_dir` | `Path` |
| `workspace_dir` | `Path` |

## `RunMetadata` fields

| Field | Type | Default |
|---|---|---|
| `run_id` | `str` | required |
| `duration_s` | `float` | required |
| `n_turns` | `int` | required |
| `n_tool_calls` | `int` | required |
| `input_tokens` | `int` | `0` |
| `output_tokens` | `int` | `0` |
| `total_cost_usd` | `float` | `0.0` |
| `is_error` | `bool` | `False` |
| `error_reason` | `Optional[str]` | `None` |
| `was_interrupted` | `bool` | `False` |
| `session_id` | `Optional[str]` | `None` |

## `OutputFile` fields + methods

| Member | Signature | Notes |
|---|---|---|
| `path` | `Path` | absolute, inside transient run workspace |
| `relative_path` | `str` | under `outputs/`, e.g. `"report.md"` |
| `size` | `int` | bytes |
| `mime` | `str` | `mimetypes.guess_type` best-effort |
| `ext` | `str` | `.md`, `.pdf`, ... |
| `read_bytes()` | `→ bytes` | |
| `read_text(encoding="utf-8")` | `→ str` | |
| `copy_to(dest)` | `→ Path` | dir → dest/relpath, non-existing → full filename |
| `move_to(dest)` | `→ Path` | same dest semantics; uses `shutil.move` |

## `ToolContext` fields (Python tools)

| Field | Type |
|---|---|
| `run_id` | `str` |
| `agent_cwd` | `Path` |
| `outputs_dir` | `Path` |
| `attachments_dir` | `Path` |
| `audit_dir` | `Path` |

(Frozen dataclass — read-only.)

## `Event` types — exhaustive

| `type` | Where emitted |
|---|---|
| `"run_started"` | `motor.py` (run start) |
| `"system_message"` | `motor.py` (CLI subprocess init/status) |
| `"assistant_text"` | `motor.py` (every text block) |
| `"thinking"` | `motor.py` (extended-thinking blocks) |
| `"tool_use"` | `motor.py` (every tool dispatch) |
| `"tool_result"` | `motor.py` (every tool return) |
| `"result"` | `motor.py` (run end) |
| `"sdk_message"` | `motor.py` (catch-all for unknown SDK msg classes) |
| `"proxy_request"` | `proxy.py` (every upstream POST) |
| `"proxy_response"` | `proxy.py` (every upstream response) |
| `"python_tool_call"` | `_python_tools.py` (every `@tool` invocation) |

## Stream chunk types (discriminator: `type`)

| Chunk class | `type` literal |
|---|---|
| `RunStartedChunk` | `"run_started"` |
| `InitChunk` | `"init"` |
| `TextDeltaChunk` | `"text_delta"` |
| `TextBlockChunk` | `"text_block"` |
| `ThinkingDeltaChunk` | `"thinking_delta"` |
| `ThinkingBlockChunk` | `"thinking_block"` |
| `ToolUseStartChunk` | `"tool_use_start"` |
| `ToolUseDeltaChunk` | `"tool_use_delta"` |
| `ToolUseCompleteChunk` | `"tool_use_complete"` |
| `ToolUseFinalizedChunk` | `"tool_use_finalized"` |
| `ToolResultChunk` | `"tool_result"` |
| `OutputFileReadyChunk` | `"output_file_ready"` |
| `ErrorChunk` | `"error"` |
| `DoneChunk` | `"done"` |

## `Motor` public methods

| Method | Returns | Notes |
|---|---|---|
| `await motor.run(task)` | `RunResult` | One-shot |
| `motor.stream(task)` | `AsyncIterator[StreamChunk]` | Streaming |
| `motor.chat(*, chat_id=None, session_id=None, root=None)` | `Chat` | Multi-turn factory |
| `await motor.console()` | `None` | Interactive REPL (needs `[console]` extras) |
| `await motor.interrupt(run_id=None)` | `bool` | Cancels in-flight run, idempotent |
| `await motor.start()` | `None` | Manual proxy start (usually lazy auto-start is fine) |
| `await motor.stop()` | `None` | Shut down proxy |
| `motor.clean_runs(*, keep_last=0, older_than_days=None, dry_run=False)` | `list[Path]` | Housekeeping |
| `motor.on_event(fn)` | `fn` | Subscribe (decorator or direct call) |
| `motor.on_log(fn)` | `fn` | Subscribe |
| `Motor.tool` | `staticmethod(tool)` | Alias for `from sophia_motor import tool` |

## `Chat` public methods

| Member | Returns | Notes |
|---|---|---|
| `chat.chat_id` | `str` | stable, persist this |
| `chat.session_id` | `str \| None` | rotates per turn, persist after each `send` |
| `chat.cwd` | `Path` (read-only property) | shared workspace root |
| `await chat.send(prompt_or_task)` | `RunResult` | accepts `str` or `RunTask` |
| `chat.stream(prompt_or_task)` | `AsyncIterator[StreamChunk]` | streaming variant |
| `await chat.reset()` | `None` | new SDK session, same `chat_id`/`cwd` |

## `EventBus` public methods

| Method | Notes |
|---|---|
| `on_event(fn)` | sync OR async subscriber |
| `on_log(fn)` | sync OR async subscriber |
| `await emit_event(event)` | swallows subscriber exceptions |
| `await emit_log(record)` | symmetrical |
| `await log(level, message, *, run_id=None, **fields)` | convenience |

## `clean_runs` signature

```python
def clean_runs(
    workspace_root: Path | str,
    *,
    keep_last: int = 0,
    older_than_days: Optional[float] = None,
    dry_run: bool = False,
) -> list[Path]:
```

`Motor.clean_runs(...)` is a bound version that forwards `self.config.workspace_root`.

## Adapter hooks

| Hook | Signature | Default |
|---|---|---|
| `name` (class attr) | `str` | `"abstract"` |
| `forward_url(base_url)` | `str → str` | `f"{base_url}/v1/messages"` |
| `forward_headers(sdk_headers, api_key)` | `(dict, str\|None) → dict` | propagate Anthropic headers |
| `verify_ssl()` | `() → bool` | `True` |
| `transform_request(body)` | `dict → dict` | passthrough |
| `transform_sse_chunk(chunk)` | `bytes → bytes` | passthrough |
| `transform_response(body)` | `dict → dict` | passthrough |

## Env vars — full list

| Env var | Field | Default if unset |
|---|---|---|
| `ANTHROPIC_API_KEY` | `api_key` | `""` |
| `SOPHIA_MOTOR_MODEL` | `model` | `"claude-opus-4-6"` |
| `SOPHIA_MOTOR_BASE_URL` | `upstream_base_url` | `"https://api.anthropic.com"` |
| `SOPHIA_MOTOR_ADAPTER` | `upstream_adapter` | `"anthropic"` |
| `SOPHIA_MOTOR_WORKSPACE_ROOT` | `workspace_root` | `~/.sophia-motor/runs` |
| `SOPHIA_MOTOR_PROXY_HOST` | `proxy_host` | `"127.0.0.1"` |
| `SOPHIA_MOTOR_CONSOLE_LOG` | `console_log_enabled` | `False` |
| `SOPHIA_MOTOR_AUDIT_DUMP` | `proxy_dump_payloads` | `False` |

Plus runtime CLI subprocess env vars (set automatically by the motor — don't need to touch):

```
CLAUDE_CONFIG_DIR                              ← per-run config dir
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING=1
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1
CLAUDE_CODE_DISABLE_AUTO_MEMORY=1
CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1
CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1
CLAUDE_CODE_DISABLE_CLAUDE_MDS=1               ← if disable_claude_md=True
CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1      ← strip bundled subagents
DISABLE_TELEMETRY=1
DISABLE_ERROR_REPORTING=1
DISABLE_AUTOUPDATER=1
DISABLE_AUTO_COMPACT=1
DISABLE_BUG_COMMAND=1
ENABLE_TOOL_SEARCH=false                       ← all tools loaded upfront
ANTHROPIC_BASE_URL=http://127.0.0.1:<port>/run/<run_id>
ANTHROPIC_AUTH_TOKEN=<api_key>
```

## When uncertain

If any field, env var, or method differs in your installed version from this table, the **source is the authority**. Inspect:

```bash
python -c "import sophia_motor.config as c; import inspect; print(inspect.getsource(c.MotorConfig))"
```

…or just `cat $(python -c "import sophia_motor, os; print(os.path.dirname(sophia_motor.__file__))")/config.py`. The skill is stale-by-design.
