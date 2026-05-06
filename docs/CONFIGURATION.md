# Configuration reference

Full surface of `MotorConfig`, `RunTask`, `RunResult`, plus the env
cascade, networking knobs, and debug knobs. The README shows the
quickstart shape; this doc is the consultable reference.

---

## Resolution cascade

For every supported field with a `SOPHIA_MOTOR_*` env var, resolution
order is:

> **explicit `MotorConfig(...)` param  >  process env var  >  `./.env` file in cwd  >  hardcoded default**

| Env var                              | Field                    | Default                                      |
|--------------------------------------|--------------------------|----------------------------------------------|
| `ANTHROPIC_API_KEY`                  | `api_key`                | (required)                                   |
| `SOPHIA_MOTOR_MODEL`                 | `model`                  | `claude-opus-4-6`                            |
| `SOPHIA_MOTOR_BASE_URL`              | `upstream_base_url`      | `https://api.anthropic.com`                  |
| `SOPHIA_MOTOR_ADAPTER`               | `upstream_adapter`       | `anthropic`                                  |
| `SOPHIA_MOTOR_WORKSPACE_ROOT`        | `workspace_root`         | `<tempdir>/sophia-motor/runs` (e.g. `/tmp/sophia-motor/runs/` on Linux) |
| `SOPHIA_MOTOR_PROXY_HOST`            | `proxy_host`             | `127.0.0.1`                                  |
| `SOPHIA_MOTOR_CONSOLE_LOG`           | `console_log_enabled`    | `false`                                      |
| `SOPHIA_MOTOR_AUDIT_DUMP`            | `proxy_dump_payloads`    | `false`                                      |
| `SOPHIA_MOTOR_PERSIST_RUN_METADATA`  | `persist_run_metadata`   | `false`                                      |

Bool env vars accept `true`/`1`/`yes`/`on` (truthy) and
`false`/`0`/`no`/`off` (falsy), case-insensitive. Anything else falls
back to the hardcoded default — typo'd values never silently coerce.

---

## Networking

The proxy listens on **127.0.0.1 with a kernel-assigned port** by default
— no exposure to the host network, no clash with services on common
ports (ollama `:11434`, vLLM, Postgres, dev servers). Inside a Docker
container the loopback is the *container's* loopback, not the host's,
so multiple motors on the same machine — or alongside any other local
service — never collide. Two `Motor()` instances in the same Python
process get distinct ports automatically.

```python
# Default — kernel picks a free port. Recommended.
Motor()

# Pin a specific port — only when you need a stable proxy URL for
# external sniffing or fixed firewall rules. Raises a clear error if
# the port is already in use.
Motor(MotorConfig(proxy_port=8765))
```

The proxy is an internal mechanism: nothing calls it from outside the
process. You never need to open a port, configure a Service, or punch
through a firewall.

---

## Workspace — ephemeral by default

`workspace_root` defaults to `<tempfile.gettempdir()>/sophia-motor/runs/` (e.g. `/tmp/sophia-motor/runs/` on Linux). The OS sweeps it on its own schedule — `systemd-tmpfiles` removes entries older than ~10 days on most Linux distros, macOS clears `/private/tmp/` at reboot, Windows runs cyclic temp cleanup. **Motor was built as a fire-and-forget intelligent function; the storage isn't a developer concern.**

For persistence (audit retention, compliance, debug post-mortems), opt in explicitly:

```python
# Persistent under your home dir — outside any repo
Motor(MotorConfig(workspace_root=Path("~/.sophia-motor/runs").expanduser()))

# Containerised — point at a mounted volume
Motor(MotorConfig(workspace_root="/data/runs"))
```

```bash
# Or via env, once per shell / process
export SOPHIA_MOTOR_WORKSPACE_ROOT=~/.sophia-motor/runs
```

`MotorConfig.workspace_root` MUST be a directory whose ancestors do NOT contain `.git/`, `pyproject.toml`, or `package.json`. The bundled Claude CLI does upward project-root discovery and would otherwise re-path session/backup state into a deeply-nested fallback. The default tempdir is always safe.

---

## Debug mode

The motor stays silent by default — no stdout, no audit dump, no `input.json`/`trace.json`, nothing written outside the per-run workspace beyond what the agent itself produces. Production-shaped out of the box. When you want to **see** what's happening, flip on the relevant debug knobs.

```bash
# inline, single run — full transparency
SOPHIA_MOTOR_CONSOLE_LOG=true \
SOPHIA_MOTOR_AUDIT_DUMP=true \
SOPHIA_MOTOR_PERSIST_RUN_METADATA=true \
python my_app.py
```

```python
# or per Motor instance
motor = Motor(MotorConfig(
    console_log_enabled=True,        # event stream to stdout
    proxy_dump_payloads=True,        # request/response bodies under <run>/audit/
    persist_run_metadata=True,       # input.json + trace.json under <run>/
))
```

| Knob | Default | What it shows |
|---|---|---|
| `console_log_enabled` | `false` | streams events as they happen — turn boundaries, tool calls, costs |
| `proxy_dump_payloads` | `false` | persists every request/response body under `<run>/audit/` so you can grep what the model actually saw and produced |
| `persist_run_metadata` | `false` | writes `<run>/input.json` (resolved RunTask snapshot) + `<run>/trace.json` (assistant blocks + final metadata) |

All three are independent. Flip on what you need; the rest stays quiet.

---

## Reasoning effort, thinking & cost killer

Three SDK-native knobs are exposed at both the motor (defaults) and task (per-run override) level. All three default to `None` — the SDK / CLI uses its own default behaviour when nothing is set.

```python
# Set defaults on the motor instance — applied to every run unless the task overrides
motor = Motor(MotorConfig(
    default_effort="low",                            # fast, minimal reasoning
    default_thinking={"type": "adaptive"},           # Claude decides depth (Opus 4.6+)
    default_max_budget_usd=5.0,                      # cost killer for the run
))

# Override per-task — full replacement, never merge
result = await motor.run(RunTask(
    prompt="...",
    effort="high",                                    # this run reasons deeper
    max_budget_usd=20.0,                              # ...with a higher ceiling
))
```

| Knob | Type | Where it goes | What it does |
|---|---|---|---|
| `effort` | `"low" \| "medium" \| "high" \| "max"` | `ClaudeAgentOptions.effort` (`--effort`) | Reasoning effort. Works alongside adaptive thinking to guide depth. Lower = faster, cheaper |
| `thinking` | `{"type": "adaptive"} \| {"type": "enabled", "budget_tokens": N} \| {"type": "disabled"}` | `ClaudeAgentOptions.thinking` (`--thinking`) | Extended-thinking config. Optional `"display": "summarized" \| "omitted"` |
| `max_budget_usd` | `float` | `ClaudeAgentOptions.max_budget_usd` (`--max-budget-usd`) | Cost killer — the run aborts with `error_max_budget_usd` once the threshold is exceeded |

Subagents have their own per-`AgentDefinition.effort` knob (re-exported from the SDK). Set it on each `AgentDefinition` if you want different effort per role:

```python
from sophia_motor import AgentDefinition

policy_analyst = AgentDefinition(
    description="...", prompt="...",
    tools=["Read", "Glob"],
    effort="low",   # this subagent doesn't need deep reasoning
)
```

**Caveats**:

- **`max_budget_usd` cost estimation matches Anthropic's published pricing.** On non-Anthropic upstreams (vLLM, custom adapter) the figure may diverge from real spend — treat the killer as best-effort there.
- **Adaptive thinking requires a model that supports it (Opus 4.6+).** On older models pass `{"type": "enabled", "budget_tokens": N}` instead, or leave it `None`.
- **Resolution**: `task.X` wins over `MotorConfig.default_X`. Both `None` → SDK / CLI default. Same convention as every other `RunTask` field.

---

## `MotorConfig`

Settings on the motor instance — set once at construction.

| Field                       | Type                                | Default                                 | What it does                                                                             |
|-----------------------------|-------------------------------------|-----------------------------------------|------------------------------------------------------------------------------------------|
| `api_key`                   | `str`                               | from `ANTHROPIC_API_KEY` env / `./.env` | Anthropic API key                                                                        |
| `model`                     | `str`                               | `"claude-opus-4-6"`                     | Default model the SDK uses                                                               |
| `upstream_base_url`         | `str`                               | `"https://api.anthropic.com"`           | Upstream endpoint the proxy forwards to                                                  |
| `upstream_adapter`          | `str` or `UpstreamAdapter`          | `"anthropic"`                           | Provider preset (`"anthropic"`, `"vllm"`) or a custom adapter instance                   |
| `workspace_root`            | `Path`                              | `<tempdir>/sophia-motor/runs/`          | Where per-run dirs are created. **Ephemeral by default** (OS-managed cleanup). Set explicitly for persistence. Must be outside any git repo / `pyproject.toml` ancestor |
| `proxy_enabled`             | `bool`                              | `True`                                  | Disable only for unit tests that mock the SDK                                            |
| `proxy_host`                | `str`                               | `"127.0.0.1"`                           | Bind host for the local proxy                                                            |
| `proxy_port`                | `int \| None`                       | `None`                                  | `None` → kernel picks free port; explicit int pins it (raises `RuntimeError` if busy)    |
| `proxy_dump_payloads`       | `bool`                              | `False`                                 | Persist every request/response under `<run>/audit/`                                      |
| `proxy_strip_sdk_noise`     | `bool`                              | `True`                                  | Strip SDK billing-header + identity blocks from system field                             |
| `proxy_strip_user_system_reminders` | `bool`                      | `True`                                  | Strip `<system-reminder>` injected by the CLI from turn ≥2 user messages                 |
| `tool_description_overrides`| `dict[str, str]`                    | `{Read: ...}`                           | Replace tool descriptions before forwarding upstream (Read defaults to a path-policy version) |
| `guardrail`                 | `"strict" \| "permissive" \| "off"` | `"strict"`                              | Built-in PreToolUse hook (see [SECURITY.md](./SECURITY.md))                              |
| `custom_pre_tool_hooks`     | `list[Any]`                         | `[]`                                    | Project-specific PreToolUse hooks composed alongside the built-in guard. Each is `async def hook(input_data, tool_use_id, context) -> dict`. Use `Allow()` / `Deny(reason)` from `sophia_motor`. Any single deny wins. See [SECURITY.md](./SECURITY.md#custom-hooks). Overridable per `RunTask` |
| `disable_claude_md`         | `bool`                              | `True`                                  | Skip auto-loading repo `CLAUDE.md` / `MEMORY.md` into the agent's context                |
| `console_log_enabled`       | `bool`                              | `False`                                 | Colored console logger for events                                                        |
| `persist_run_metadata`      | `bool`                              | `False`                                 | Write `<run>/input.json` (resolved RunTask snapshot) + `<run>/trace.json` (assistant blocks + metadata). Independent from `proxy_dump_payloads` (which gates `<run>/audit/`) |
| `cli_bare_mode`             | `bool`                              | `False`                                 | Pass `--bare` to the CLI subprocess (advanced; breaks the Skill tool — see source)       |
| `cli_no_session_persistence`| `bool`                              | `True`                                  | Pass `--no-session-persistence` (no `session.jsonl` written by the CLI)                  |
| `cli_strict_mcp_config`     | `bool`                              | `True`                                  | Pass `--strict-mcp-config` — only the SDK-passed MCP servers (your `@tool` functions) reach the model. Skips ambient discovery: `.mcp.json` walk, user-settings MCP, plugin MCP, `claude.ai` proxy connectors. Auto-skipped in chat-mode |
| `default_system`            | `str?`                              | `None`                                  | Default system prompt applied when `RunTask.system` is `None`                            |
| `default_tools`             | `list[str]?`                        | `[]`                                    | Default hard tool whitelist; `None` = SDK's `claude_code` preset (every built-in)         |
| `default_allowed_tools`     | `list[str]?`                        | `None`                                  | Default permission-skip list                                                             |
| `default_disallowed_tools`  | `list[str]`                         | `DEFAULT_DISALLOWED_TOOLS` (17 entries) | Tools blocked by default: web access, `Agent`, plan-mode, cron, MCP auth flows, ...      |
| `default_skills`            | `Path \| str \| list?`              | `None`                                  | Default skill source(s)                                                                  |
| `default_attachments`       | `Path \| dict \| list?`             | `None`                                  | Default attachments                                                                      |
| `default_disallowed_skills` | `list[str]`                         | `[]`                                    | Skills blocked by default                                                                |
| `default_max_turns`         | `int`                               | `20`                                    | Default per-task turn cap                                                                |
| `default_timeout_seconds`   | `int`                               | `300`                                   | Default per-task timeout                                                                 |
| `default_max_budget_usd`    | `float?`                            | `None`                                  | Default cost killer in USD — the run aborts with `error_max_budget_usd` once the threshold is exceeded. Estimation matches Anthropic pricing; best-effort on non-Anthropic upstreams |
| `default_thinking`          | `dict?`                             | `None`                                  | Default extended-thinking config: `{"type": "adaptive"}` (Opus 4.6+ default) / `{"type": "enabled", "budget_tokens": N}` / `{"type": "disabled"}`. Optional `"display"` key |
| `default_effort`            | `"low" \| "medium" \| "high" \| "max" ?` | `None`                             | Default reasoning effort. Works with adaptive thinking. Subagents have their own `AgentDefinition.effort` |
| `default_output_schema`     | `type[BaseModel]?`                  | `None`                                  | Default Pydantic class for structured output                                             |
| `default_agents`            | `dict[str, AgentDefinition]`        | `{}`                                    | Default subagents (forwarded to `ClaudeAgentOptions.agents`); requires `"Agent"` in `tools` to actually take effect |

---

## `RunTask`

Settings on the single call — passed to `motor.run(RunTask(...))`. Anything left unset falls back to the matching `MotorConfig.default_*`.

| Field               | Type                    | What it does                                                                                                                                                                                                                                   |
|---------------------|-------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `prompt`            | `str`                   | **Required.** The user-message instruction                                                                                                                                                                                                     |
| `system`            | `str?`                  | System prompt for this task (overrides `default_system`)                                                                                                                                                                                       |
| `tools`             | `list[str]?`            | Hard whitelist of tools the model can SEE. `[]` = no tools, `None` = fall back to `MotorConfig.default_tools`                                                                                                                                  |
| `allowed_tools`     | `list[str]?`            | Permission skip — rarely needed: the motor runs with `permission_mode="bypassPermissions"`                                                                                                                                                     |
| `disallowed_tools`  | `list[str]?`            | Tools hard-blocked from the model's context                                                                                                                                                                                                    |
| `max_turns`         | `int?`                  | Per-task turn cap (overrides default)                                                                                                                                                                                                          |
| `attachments`       | `Path \| dict \| list?` | Inputs the agent can read. File `Path` → hard-linked (zero-copy, glob-visible), directory `Path` → mirrored as real dirs with file-level hard-links, `dict[str,str]` → inline file. Symlink fallback on cross-filesystem. Mixed list supported |
| `skills`            | `Path \| str \| list?`  | Skill source folder(s). Each subdir with `SKILL.md` is linked into the run                                                                                                                                                                     |
| `disallowed_skills` | `list[str]`             | Skill names to skip even if found in source                                                                                                                                                                                                    |
| `agents`            | `dict[str, AgentDefinition]?` | Per-task subagent overrides. `None` falls back to `MotorConfig.default_agents`. `{}` explicitly disables. Requires `"Agent"` in `tools`.                                                                                              |
| `custom_pre_tool_hooks` | `list[Any]?`        | Per-task PreToolUse hooks. `None` (default) falls back to `MotorConfig.custom_pre_tool_hooks`; a list **fully replaces** the config (NOT merge); `[]` explicitly drops the config defaults for this run. Built-in guardrail still runs. See [SECURITY.md](./SECURITY.md#custom-hooks) |
| `max_budget_usd`    | `float?`                | Cost killer in USD for this run. `None` → fall back to `MotorConfig.default_max_budget_usd`. See "Reasoning effort, thinking & cost killer" above                                                                                              |
| `thinking`          | `dict?`                 | Extended-thinking config for this run. `None` → fall back to `MotorConfig.default_thinking`. Same shape as the SDK's `ThinkingConfig`                                                                                                          |
| `effort`            | `"low" \| "medium" \| "high" \| "max" ?` | Reasoning effort for this run. `None` → fall back to `MotorConfig.default_effort`. Subagent effort is set per `AgentDefinition.effort`                                                                                  |
| `output_schema`     | `type[BaseModel]?`      | Pydantic class — agent commits to this shape, returned in `RunResult.output_data`                                                                                                                                                              |
| `session_id`        | `str?`                  | Resume an existing SDK session (chat-style). Most callers use `Chat` instead.                                                                                                                                                                  |
| `workspace_dir`     | `Path?`                 | Pre-existing chat workspace to reuse. Set by `Chat` for multi-turn dialogs.                                                                                                                                                                    |

---

## `RunResult`

What `motor.run(...)` returns.

| Field           | Type          | What it is                                                                                    |
|-----------------|---------------|-----------------------------------------------------------------------------------------------|
| `run_id`        | `str`         | `run-<unix>-<8hex>`                                                                           |
| `output_text`   | `str?`        | Final assistant text (free-form)                                                              |
| `output_data`   | `BaseModel?`  | Schema-validated payload, present iff `output_schema` was set                                 |
| `output_files`  | `list[OutputFile]` | Files the agent wrote under `outputs/` (with `.copy_to(...)` to persist)                |
| `metadata`      | `RunMetadata` | `n_turns`, `n_tool_calls`, tokens, `total_cost_usd`, `duration_s`, `is_error`, `error_reason`, `was_interrupted`, `session_id` |
| `audit_dir`     | `Path`        | `<run>/audit/` (`request_*.json` + `response_*.sse`)                                          |
| `workspace_dir` | `Path`        | The full run dir                                                                              |
| `blocks`        | `list`        | Raw assistant blocks (text + tool_use + thinking)                                             |
