# Security — guardrail, sandbox, what's blocked

The motor ships a built-in `PreToolUse` hook that sandboxes the agent inside its workspace. Three modes via `MotorConfig.guardrail`:

```python
MotorConfig(guardrail="strict")        # default — safe-by-default
MotorConfig(guardrail="permissive")    # sane minimums only
MotorConfig(guardrail="off")           # no hook at all
```

## Strict mode (default)

Per-tool checks before invocation:

| Tool | Strict check |
|---|---|
| `Read` | `os.path.normpath(file_path)` must be a prefix of cwd. **Lexical**, not symlink-followed (so motor-managed symlinks in `attachments/` keep working) |
| `Edit` | same |
| `Glob`, `Grep` | path parameter must be lexically under cwd |
| `Write` | `Path.resolve()` of `file_path` must be under `<cwd>/outputs/`. Absolute paths and symlink escapes blocked |
| `Bash` | Heavy blocklist (see below) |

Strict-mode `Bash` blocklist (refused by command word):

```
curl wget ssh scp rsync ftp telnet nc ncat netcat
git docker podman kubectl helm
pip pip3 npm yarn pnpm cargo go
sudo doas su pkexec
chmod chown chgrp
ln link symlink           ← can't create new symlinks (closes attachment escape)
mount umount
mkfs dd
```

Plus pattern-based blocks:
- `..` in any argument (path escape)
- `/dev/tcp/...`, `/dev/udp/...` (Bash backdoor sockets)
- `bash -c ...`, `eval ...`, `exec ...`, `source ...` (re-entrancy escape)
- **Python invocation guard** (added in 0.4.5):
  - `python -c "..."` only allowed with stdlib-safe imports — refuses `os`, `subprocess`, `exec`, `eval`, `__import__` in the inline code
  - `python <script>` only allowed if the script is a registered skill helper (lives under a mounted skill folder). `python outputs/x.py` and `python attachments/x.py` are blocked — closes the `Write` + `python …` workaround for sandboxed shell escape

## Permissive mode

Only sane minimums survive:

- `..` path escapes still blocked
- `sudo` / `doas` / `su` / `pkexec` blocked (privilege escalation)
- `curl --data` / `wget --post-data` blocked (data exfiltration)
- `/dev/tcp/...` blocked
- `rm -rf /` and friends blocked (destructive)
- `dd`, `mkfs`, `mount` blocked

Use when the agent **legitimately** needs git, docker, package managers (e.g. a build agent). Don't reach for `permissive` to "make tests pass" — that's a sign the agent is doing something the strict mode is right to refuse.

## Off mode

No hook registered. The SDK's native permission system is the only thing left. Use only when:

- You **fully trust** the prompt (programmatic, not user-supplied).
- AND you fully trust the host (ephemeral container, no production secrets, no escape risk).

Prototypes and sandboxed Docker runs may use `off`. **Production with user-supplied prompts: always strict.**

## Guard refusal behaviour

When the guard refuses, it returns `{"decision": "block", "reason": "..."}` to the SDK. The SDK passes the reason to the model as a system feedback message. Verified empirically:

```python
# Prompt: "Read /etc/passwd"
# → Guard: BLOCKED Read /etc/passwd: not under cwd
# → Model: "I'm sandboxed and can't access /etc/passwd."
```

The model gracefully degrades — it doesn't crash, it adapts.

## Why lexical (not symlink-following) for Read/Glob

Motor creates intentional symlinks in `attachments/` for zero-copy. A `Path.resolve()` check would reject them (target outside cwd). Anti-symlink-escape is moved to **Bash**: `ln`, `link`, `symlink` blocked → the model can't **create** symlinks at runtime. The motor's own materialization runs **before** the agent starts, so motor-created links are present from the start, and the agent can read through them via `Read` (which doesn't check symlink targets).

## What strict mode does NOT do (yet)

Future work, not currently implemented:
- Rate limit per-tool / per-run
- Content filter on prompts (PII, prompt-injection detection)
- Per-run filesystem sandbox managed via chroot / unshare / overlayfs
- Sandboxed Bash with allowlist-only (today: blocklist; allowlist would be safer in the limit)
- Guard on tool **output** (model writes a path outside cwd in a `tool_result` → block at return)

If your threat model needs these, layer on top of the motor (separate process, namespaces, AppArmor) — don't expect the in-process guard to do everything.

## Custom hooks (advanced)

Today there's no API for user-supplied hooks alongside the builtin. If you need custom checks (e.g. "block reading PDFs of other programs"), set `guardrail="off"` and patch `_build_sdk_options` in your fork to register your own hook. Adding `MotorConfig.custom_pre_tool_hook` is on the roadmap (Tier 2 in `gap_real_use.md`).

## Default disallowed_tools (the second layer)

Even with strict mode, `MotorConfig.default_disallowed_tools` removes whole tools from the model's context. Default list (in `config.py:DEFAULT_DISALLOWED_TOOLS`):

```
WebFetch, WebSearch                                  ← live internet
AskUserQuestion, TodoWrite, Agent                    ← agentic / interactive
EnterPlanMode, ExitPlanMode                          ← planning UI
TaskOutput, TaskStop                                 ← task IPC
EnterWorktree, ExitWorktree                          ← git worktrees
CronCreate, CronDelete, CronList                     ← scheduling
Monitor, PushNotification, ScheduleWakeup            ← IDE-style
NotebookEdit, RemoteTrigger                          ← misc
mcp__claude_ai_*__authenticate (+ complete)          ← MCP auth flows we don't configure
```

To opt one back in for a specific run: list it in `tools=[...]`. The motor's conflict-resolution drops it from disallowed for that run only, leaving the rest of the block list intact.

## Audit trail = the security log

Every tool invocation is recorded in `<run>/audit/`:

- `request_NNN.json` — the body sent upstream (system + tools + messages, every turn)
- `response_NNN.{sse,json}` — the model's reply
- `tool_<name>_NNN.json` — every Python `@tool` invocation (input + output + error + duration_ms)

When investigating a security incident, this is the ground truth. The model can lie in `output_text`; the audit log can't.

## Live verification

There's no dedicated security example, but every existing example runs under strict mode by default. The guard refuses are visible in the proxy log if you set `console_log_enabled=True` and ask the agent to do something out-of-bounds.

## When uncertain

- "Why is the agent saying it can't read X?" → Check the guard log. `os.path.normpath(X)` may not start with cwd. The guard is doing its job.
- "How do I let the agent install a pip package?" → You usually don't. If you really do (e.g. a build agent), set `guardrail="permissive"`, add `"Bash"` to `tools`, and document why in the code.
- "Can the agent exfiltrate data?" → In strict mode, no obvious path: no `curl`, no `wget --post-data`, no `/dev/tcp`, no `python -c "import urllib"`, no `python outputs/x.py`. Static blocklists aren't perfect — the OS-level sandbox (chroot / userns) is the real defense; the in-process guard is the fast first line.
- Need to read `guard.py` directly? It's ~450 lines, all readable. Search `make_guard_hook(mode)` for the entry point.
