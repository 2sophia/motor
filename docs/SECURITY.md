# Security model

The motor's security stance, written honestly. The strict guard is a
**lexical first filter** — it catches the common LLM mistake and the
naïve prompt injection. It is **not** a formal sandbox. For real
production use, layer OS-level isolation underneath (see
[Production hardening](#production-hardening) below).

---

## Three modes

| Mode           | Read / Edit / Glob / Grep | Write           | Bash                                                                                                                     |
|----------------|---------------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------|
| **strict** (default) | must stay inside cwd | only `outputs/` | dev/admin commands blocked (`curl`, `git`, `docker`, `pip`, `npm`, `sudo`, ...) + `..` / `/dev/tcp` / `bash -c` / `eval` + Python invocation parser |
| **permissive** | unrestricted              | unrestricted    | only `sudo`, exfiltration patterns, `/dev/tcp`, `..` escapes, destructive commands                                       |
| **off**        | unrestricted              | unrestricted    | unrestricted                                                                                                             |

```python
Motor(MotorConfig(guardrail="strict"))      # default — safe by default
Motor(MotorConfig(guardrail="permissive"))  # blocks only sudo/exfil/escapes
Motor(MotorConfig(guardrail="off"))         # no hook (you take responsibility)
```

---

## What the motor controls (that the raw SDK doesn't)

The Claude Agent SDK ships a CLI that, by default, inherits the entire
environment of your Python process and runs Bash freely. If you embed
the raw SDK in a backend that has `MONGODB_URI`, `STRIPE_SECRET_KEY`,
or `AWS_ACCESS_KEY_ID` in its env, **the model can read them** with a
single `os.environ` print or `env` shell command. The motor closes the
common gaps:

| Layer | Raw SDK | sophia-motor |
|---|---|---|
| Subprocess env | full inherit (host secrets visible) | **only** `PATH`, `ANTHROPIC_API_KEY`, `CLAUDE_CONFIG_DIR`, model + `DISABLE_*` flags. Nothing else leaks |
| Filesystem reads | unrestricted | `Read/Edit/Glob/Grep` fenced inside the run cwd (strict) |
| Filesystem writes | unrestricted | `Write` restricted to `outputs/` (strict), with symlink-escape resolution |
| Bash blocklist | none | dev/admin commands + `bash -c` + `..` + `/dev/tcp` + `eval`/`source`/`exec` redirects |
| Exfiltration patterns | none | `curl`/`wget` with `--data`/`--upload-file` blocked in **both** strict and permissive |
| MCP discovery surface | reads `.mcp.json` upward from cwd, user-settings MCP, plugin MCP, `claude.ai` proxy connectors (Gmail, Slack, Datadog, ...) | `cli_strict_mcp_config=True` (default): **only** the SDK-passed `@tool` functions reach the model. All ambient MCP sources skipped |
| Per-run isolation | shared cwd | each run gets its own workspace under `<workspace_root>/<run_id>/`. **Default ephemeral** in `<tempdir>/sophia-motor/runs/` — OS sweeps it (`systemd-tmpfiles`, reboot, cyclic) |
| Audit trail | none | every request/response body persisted under `<run>/audit/` (when `proxy_dump_payloads=True`) |
| Custom policy hooks | none | `MotorConfig.custom_pre_tool_hooks` + `RunTask.custom_pre_tool_hooks` for project-specific bans on top of the strict floor (see below) |

---

## Python invocation guard (strict mode only)

`python` and `python3` are allowed in strict mode but the call shape
is constrained:

| Form | Verdict |
|---|---|
| `python -c "<code>"` with stdlib-safe imports + no `os`/`subprocess`/`socket`/`shutil`/`exec`/`eval`/`__import__`/`open('/abs/path')` | ✅ allowed |
| `python <path>` where `<path>` is under `$CLAUDE_CONFIG_DIR/skills/<name>/scripts/` (a skill the dev registered) | ✅ allowed |
| `python -c "..."` with `import os`, `subprocess`, `shutil`, `socket`, `urllib`, `requests`, `__import__(...)`, `exec(...)`, `eval(...)`, `open('/abs/path')`, `open(0)`, `__builtins__`, `getattr(...)` | ❌ blocked |
| `python outputs/foo.py`, `python attachments/foo.py`, `python /tmp/foo.py` | ❌ blocked (Write+exec workaround closed) |
| `python` (REPL), `python -m <anything>`, `python -i ...`, `python -V`, `python < /dev/stdin`, `cat foo.py \| python` | ❌ blocked |

Stdlib whitelist for `python -c` imports: `math`, `statistics`,
`decimal`, `fractions`, `json`, `re`, `datetime`, `random`,
`itertools`, `functools`, `collections`, `string`, `textwrap`,
`unicodedata`, `base64`, `hashlib`, `uuid`, `time`, `operator`,
`copy`, `enum`, `typing`. Anything else needs to live as a registered
skill — that's the trust passport.

**Skill = capability bounded.** The dev decides "my agent can query
Qdrant" by writing a `query-qdrant` skill with its own
`scripts/search.py`. The agent runs that script through the
skill-script whitelist; it cannot import `qdrant_client` directly via
`python -c`. Strict stays strict — no flag explosion needed.

In permissive mode the python-c whitelist does **not** apply: the dev
has signed off on trusted-tool tier and any `python` call is fine
(other than the cross-mode escapes like `bash -c`, `eval`, `/dev/tcp`,
`| python`, ...).

---

## Custom hooks

The strict floor is the universal sandbox. **Project-specific policies** live on top — pass `async def` callbacks to `MotorConfig.custom_pre_tool_hooks` (or `RunTask.custom_pre_tool_hooks` for per-call overrides). They run **after** the built-in guard in the same `PreToolUse` matcher; **any single deny wins** (logical AND of allow).

```python
from sophia_motor import Allow, Deny, Motor, MotorConfig, RunTask


async def secrets_policy(input_data, tool_use_id, context):
    """Block any Read/Edit of paths that look like secret material."""
    if input_data["tool_name"] not in ("Read", "Edit", "Glob", "Grep"):
        return Allow()
    path = input_data["tool_input"].get("file_path") or input_data["tool_input"].get("path") or ""
    forbidden = ("secrets", "credentials", ".pem", ".key", "password")
    for token in forbidden:
        if token in path.lower():
            return Deny(reason=(
                f"Path '{path}' is blocked by the secrets policy "
                f"(contains '{token}'). Read non-secret files only."
            ))
    return Allow()


motor = Motor(MotorConfig(
    guardrail="strict",                         # built-in floor stays on
    custom_pre_tool_hooks=[secrets_policy],     # composed on top
))
```

`Allow()` returns the empty dict (signals "let it through"). `Deny(reason=...)` builds the **modern PreToolUse return shape** (`hookSpecificOutput.permissionDecision="deny"` + `permissionDecisionReason`, with the legacy `decision: "block"` field kept for max CLI back-compat). Both helpers are exported from `sophia_motor` directly — you don't need to remember the field names.

### Per-task overrides

Pass a different policy list for one specific run via `RunTask.custom_pre_tool_hooks`. Same convention as every other `RunTask` field: `None` falls back to the config; a list **fully replaces** the config (never merges); `[]` explicitly drops the config defaults for this single call.

```python
motor = Motor(MotorConfig(
    custom_pre_tool_hooks=[default_policy],    # the floor for every run
))

# Stricter policy for one task — default_policy is REPLACED, not extended.
await motor.run(RunTask(
    prompt="...",
    tools=["Read", "Bash"],
    custom_pre_tool_hooks=[stricter_secrets_policy],
))

# Zero custom hooks for this task — only the built-in guardrail runs.
await motor.run(RunTask(prompt="...", custom_pre_tool_hooks=[]))

# Inherits from config (no field passed) — default_policy applies.
await motor.run(RunTask(prompt="..."))
```

### Why we don't wrap user hooks

The motor **does not** wrap your hook with a fail-open shim that would catch garbage returns and silently default to allow. For security-critical paths, `fail-loud > fail-open`: if your hook returns `None` (forgotten `return`), the SDK crashes the run and you see the bug in 5 minutes. A "tolerant" wrapper would let a missing `return` silently allow the very call you meant to deny — a warning in production logs that nobody reads. Use the `Allow()` / `Deny(reason=...)` helpers and write your hooks with `return` on every path; the SDK does the right thing if you slip up.

### Worked example

`examples/custom-guard/main.py` ships two hooks composed in one list (a secrets-path policy + a Bash-on-system-logs policy) and runs both an allowed and a denied scenario live, showing the deny reason flowing back to the model verbatim. Read it for the canonical pattern.

---

## What the motor still does NOT control

- **Skill scripts are trusted code** (yours). The motor symlinks
  whatever you put under `default_skills` into the run. If a skill's
  `scripts/foo.py` does something destructive, the guard won't catch
  it — the dev who registered the skill has signed off on it.
- **The `Skill` tool itself is a code-execution surface** by design.
  Strip it from `tools` if your trust boundary doesn't include
  whoever wrote the skills.
- **`guardrail="off"`** is opt-in escape hatch. Use only inside an
  ephemeral container or a dedicated VM where blast radius is the
  container itself.
- **Determined evasion** via heavy obfuscation (custom encoding +
  `compile()` chains, ctype tricks via skills, etc.) is still
  possible. The guard defeats the common prompt-injection and
  honest-mistake cases — it is not a formal sandbox.
- **Other interpreters** beyond Python (`lua`, `tcl`, `julia`, `R`,
  `php -r`, `awk 'BEGIN{system(...)}'`, `sed 'e ...'`, future
  runtimes) are not all individually parsed. The blocklist catches
  the common ones (`node`, `ruby`, `perl`, `pwsh`); rare/exotic
  interpreters can slip through if you make them available in `PATH`.
  The guard is a **lexical first filter**, not an exhaustive runtime
  registry.

---

## Production hardening

The strict guard catches the common LLM mistake and the naïve prompt-
injection. It is **not** a sandbox you can rely on alone. For anything
that touches real users or real secrets, layer OS-level isolation
underneath:

```
Container (Docker, k8s, Firecracker, ...)
  └─ non-privileged user (UID ≥ 1000), no sudo, no setuid bits
     └─ read-only filesystem, except /data (volume) and /tmp (tmpfs)
        └─ no outbound network (or NetworkPolicy / iptables egress allowlist)
           └─ dropped Linux capabilities (--cap-drop=ALL, then add only what's needed)
              └─ resource limits (--memory, --cpus, --pids-limit)
                 └─ then the motor with guardrail="strict"
```

Each layer covers a different threat:

| Layer | What it stops | Without it... |
|---|---|---|
| Non-priv user | `sudo`, `chmod` on system files, mount, kill other processes | guard's `sudo` block isn't enough — root can still escape |
| Read-only FS | `Write`/`shutil.rmtree` on system paths, planted persistent files | guard restricts `Write` to `outputs/` but a bug = host damage |
| No outbound network | Exfiltration of secrets the env strip didn't catch | env strip is best-effort, network gate is binary |
| Dropped capabilities | `mount`, `setuid`, raw socket | `CAP_NET_RAW` would let the agent skip our network gate |
| Resource limits | Fork bombs, CPU/memory exhaustion DoS | the guard doesn't measure resource usage |

For an `examples/docker/` starting point with most of these baked in,
see [examples/docker/](../examples/docker/). For Kubernetes, use a
`securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`,
`capabilities.drop: [ALL]`) and a `NetworkPolicy` denying egress.

The guard saves you from the easy 95%. The OS layer is what keeps the
remaining 5% from blowing up. **Use both — you need both.**
