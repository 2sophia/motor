# subagents — declarative

Define specialists on `MotorConfig.default_agents`. The model reads each
subagent's `description` and chooses which one to spawn based on the
prompt. **No explicit "use the X agent" needed** — natural fit for chat
backends where the caller doesn't know in advance which specialist will
be relevant.

```python
motor = Motor(MotorConfig(
    default_agents={
        "code-reviewer": AgentDefinition(description="...", prompt="...", tools=[...]),
        "doc-checker":   AgentDefinition(description="...", prompt="...", tools=[...]),
    },
    default_tools=["Read", "Grep", "Glob", "Agent"],   # Agent must be in tools
))

# Same prompt, the model picks per-task based on the descriptions.
await motor.run(RunTask(prompt="Look at calc.py: docs match? code quality?"))
```

## Why opt-in is explicit

The motor's `default_disallowed_tools` includes `"Agent"` by design — so a
plain `Motor()` cannot spawn subagents until the dev declares it wants
them. **Two explicit moves** enable subagents:

1. `default_agents={...}` (or per-task `agents={...}`)
2. `"Agent"` in `default_tools` (or per-task `tools`)

When `Agent` is whitelisted in `tools`, the motor's conflict-resolution
removes it from the default disallowed block automatically. The other
~16 default blocks (WebFetch, WebSearch, TodoWrite, Monitor, ...) stay
active. Without the two moves, `motor.run()` raises a `RuntimeError`
with a clear message pointing at what's missing.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

The model reads the prompt, looks at the two `description` strings, and
spawns one or both subagents in sequence. The parent conversation only
receives each subagent's final summary — the file reads / grep results
stay inside the subagent's isolated context. Token usage is roughly
"main agent + N subagents" worth of conversations.
