# custom-guard

Bolt project-specific PreToolUse rules onto the motor without forking it.

## When you need this

The built-in `MotorConfig.guardrail` (`"strict"` / `"permissive"` / `"off"`) covers the standard sandbox: keep file ops inside the run cwd, block `Write` outside `outputs/`, ban dev/admin commands in Bash, refuse `..` path escapes. That's the universal floor.

What it can't know is **your project's policy**:

- "block any `Read` of files under `attachments/secrets/`"
- "ban `Write` of files matching `*.pem`, `*.key`, `*.env.production`"
- "deny `Bash` commands that touch `/var/log/*` or `journalctl`"
- "the `Read` tool can only see files mentioned in this allow-list"

For these you write a small `async def` callback and pass it in `MotorConfig.custom_pre_tool_hooks`. The motor composes it with the built-in guard automatically — both run, **any single deny wins**.

## The shape

```python
from sophia_motor import Allow, Deny, Motor, MotorConfig, RunTask


async def my_policy(input_data: dict, tool_use_id, context) -> dict:
    """PreToolUse hook signature (matches claude-agent-sdk)."""
    if input_data["tool_name"] == "Read":
        path = input_data["tool_input"].get("file_path", "")
        if "secrets" in path.lower():
            return Deny(reason=f"Path '{path}' is blocked by the secrets policy.")
    return Allow()


motor = Motor(MotorConfig(
    guardrail="strict",                       # built-in guard stays on
    custom_pre_tool_hooks=[my_policy],        # composed alongside it
))
```

`Allow()` is sugar for `{}`. `Deny(reason=...)` builds the modern PreToolUse return shape (`hookSpecificOutput.permissionDecision="deny"` + the legacy `decision="block"` for max CLI back-compat). Both helpers come from `sophia_motor` directly.

## Run the bundled example

```bash
pip install sophia-motor
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
python main.py
```

`main.py` ships **two** custom hooks — `secrets_policy` (denies path tokens like "secrets", "credentials", `.pem`, `.key`) and `deny_log_writes` (denies Bash that touches `/var/log/*`). Both go in the same `custom_pre_tool_hooks` list. Two test runs:

1. The agent reads `attachments/note.txt` → allowed, answers correctly with the threshold rate.
2. The agent attempts `attachments/secrets/api_keys.txt` → denied by `secrets_policy` → the model receives the deny reason verbatim and tells the user *"It appears that the file is blocked by a secrets policy."*

## What you get for free

- **The built-in guard still runs** — your hook only adds rules, never replaces the strict-mode floor (unless you set `guardrail="off"` explicitly).
- **The deny reason flows back to the model** through the CLI's `blockingError`. Whatever string you pass to `Deny(reason=...)` is what the model sees and reasons about.
- **You can compose multiple hooks**. They all live in the same list, all run, the first deny stops the chain.
- **Hooks see real input**: `input_data["tool_name"]`, `input_data["tool_input"]`, plus the agent's `cwd`. Inspect anything.

## What `Deny` is *not*

It's not a hard kill switch — it tells the **model** "this didn't fly, try something else". The model gets the reason and either retries with a different approach or admits to the user that the resource is off-limits (your job to write a clear, actionable reason).

For an unrecoverable hard stop on a downstream condition, look at `motor.interrupt()` (caller-side) or the streaming `DoneChunk.metadata.was_interrupted` flag.

## Patterns we recommend

- **Make `reason` actionable.** Bad: `"Denied"`. Good: `"Path 'X' is blocked by the secrets policy (matches 'credentials'). Read non-secret files only — e.g. attachments/note.txt."` The model will paraphrase your reason to the user, so write it like a teammate's review comment, not a stack trace.

- **Group related rules in one hook.** A single `secrets_policy` covering 5 token tests is easier to read and unit-test than 5 micro-hooks.

- **Allow first, deny last.** If the tool isn't one of the ones you care about, return `Allow()` early. Lets the rest of the chain (built-in + other custom hooks) decide.

- **Unit-test your hook.** It's pure: `await my_policy({"tool_name": "Read", "tool_input": {"file_path": "..."}}, None, None)`. Assert on the return shape (`out.get("decision")`, `out["reason"]`).

## See also

- [`security.md`](https://github.com/2sophia/skills/blob/main/skills/sophia-motor/security.md) in the skill repo for the strict/permissive/off policy reference.
- [`MotorConfig.custom_pre_tool_hooks`](../../src/sophia_motor/config.py) docstring for the full description.
