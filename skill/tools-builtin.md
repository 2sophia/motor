# Built-in tools

The Claude CLI ships ~10 native tools. The motor exposes them as **string names** in `default_tools` / `RunTask.tools`. **Default whitelist is `[]`** — the agent gets nothing unless you opt in (golden rule #4).

## The whitelist

| Tool | What it does | Notes |
|---|---|---|
| `"Read"` | Read a text file under cwd | Path policy enforced via `tool_description_overrides` (see below) |
| `"Edit"` | Modify an existing file | Pair with `Read` so the model knows what it's editing |
| `"Write"` | Create files | Strict guardrail confines to `outputs/` only |
| `"Glob"` | Pattern match filenames | `ripgrep --files` under the hood — see attachments.md for symlink caveat |
| `"Grep"` | Pattern match file content | Same engine |
| `"Bash"` | Run shell commands | Heavily filtered in strict mode (no curl/wget/git/sudo/pip/npm) |
| `"Skill"` | Invoke a `SKILL.md` skill | Pair with `skills=...` |
| `"WebSearch"` | Live internet search | In `default_disallowed_tools` — opt in per-run only |
| `"WebFetch"` | Fetch a URL → text/markdown | Same |
| `"Agent"` | Spawn a subagent | In `default_disallowed_tools` — see [`subagents.md`](subagents.md) |

## Setting them

```python
motor = Motor(MotorConfig(
    default_tools=["Read", "Glob", "Grep"],     # apply to every run
))

# or per-task
result = await motor.run(RunTask(
    prompt="...",
    tools=["Read"],     # this run sees ONLY Read; full replacement
))
```

## Tool descriptions the model sees

The CLI ships verbose, IDE-oriented descriptions for the built-ins. The motor overrides the noisy parts via `MotorConfig.tool_description_overrides: dict[str, str]` (in 0.5.x: only `Read` is overridden by default, to enforce relative paths under the sandboxed cwd). Add your own program-specific overrides:

```python
MotorConfig(tool_description_overrides={
    **DEFAULT_TOOL_DESCRIPTION_OVERRIDES,    # keep the Read override
    "Bash": "You may run pandas/numpy/pdf scripts only — no shell commands.",
})
```

To disable all overrides: `tool_description_overrides={}`.

## Conflict resolution: tools vs disallowed_tools

The motor ships a sensible `default_disallowed_tools` (web access, agentic spawning, IDE-style noise — full list in [`reference.md`](reference.md)). When you whitelist a tool that's also in the disallowed list, the motor's conflict resolution **drops it from disallowed for this run**:

```python
# Web search defaults to blocked. To use it:
motor.run(RunTask(
    prompt="What's today's NYT front page?",
    tools=["WebSearch", "WebFetch"],     # auto-removed from disallowed for this run
))
```

You don't need to touch `disallowed_tools` to "make it allowed". **Don't** wipe the whole disallowed list (`default_disallowed_tools=[]`) — you'd unblock 17+ tools you didn't intend to.

The same applies to `"Agent"` for subagents: just whitelist it in `tools` ([`subagents.md`](subagents.md)).

## What happens with `default_tools=None`

Special escape hatch: `default_tools=None` (instead of `[]`) → the SDK's `claude_code` preset is used (every built-in available). Almost never what you want; documented for completeness.

## When to opt for a built-in vs a custom @tool

| Use case | Built-in | Custom `@tool` |
|---|---|---|
| Read repo files / docs | `"Read"` + `"Glob"` | — |
| Look up internal DB row | — | `@tool` calling your ORM |
| Run a one-off bash pipeline | `"Bash"` | — |
| Compute a typed result with validation | — | `@tool` returning Pydantic |
| Persist a file with structured filename | `"Write"` | `@tool(ctx)` writing to `ctx.outputs_dir` (deterministic location, audit trail) |
| Live web search | `"WebSearch"` + `"WebFetch"` | `@tool` wrapping Google / Brave / SerpAPI client |

In general: **prefer custom `@tool` for any I/O the model shouldn't see implementation of**. Built-ins are great for filesystem exploration; custom tools are great for domain logic.

## Cost note

Every tool you list costs tokens (the description goes into the system prompt every turn). For minimum cost on pure reasoning tasks, leave `default_tools=[]`. Empirically: `tools=[]` vs `tools=None` (preset) is a -94% cost difference on quickstart-grade prompts (`$0.0030` vs `$0.0498`).

## Live test it

Live verification with actual API calls lives in [`examples/`](../examples/) — `attachments/`, `web-search/`, `file-creation/` etc. each demonstrate a single tool family. Skim the example's `main.py` before assuming a tool's behaviour from this doc.
