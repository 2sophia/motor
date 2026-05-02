# subagents — built-in `general-purpose`

Sometimes you don't need specialists, you just want **context isolation**:
let the agent explore a folder, read many files, run several greps,
WITHOUT that content piling up in the main conversation.

When `Agent` is in `tools` and you do **not** define any custom agents,
the Claude SDK exposes the built-in `general-purpose` subagent. The
main agent can delegate exploration to it; only the final summary
returns to the parent.

```python
motor = Motor(MotorConfig(
    # No `default_agents={...}` — just expose the Agent tool.
    default_tools=["Read", "Glob", "Grep", "Agent"],
    default_disallowed_tools=[],
))

await motor.run(RunTask(
    prompt="Explore X and summarize the public entry points."
))
```

## Why this is useful

A 200-file codebase exploration WITHOUT the subagent dumps every file's
content into the main conversation — token cost explodes, the main
context fills with low-signal data, and any follow-up reasoning has to
work around the noise. With the subagent, the parent receives a single
summary message — far cheaper and cleaner.

Trade-off: each subagent run starts a fresh conversation, so you pay
some setup cost. The break-even is roughly "more than 4-5 file reads"
in the subagent vs. doing them inline.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```
