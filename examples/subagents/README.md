# subagents

The motor exposes Anthropic's subagent dispatch via `MotorConfig.default_agents` (or per-task `RunTask.agents`). The model spawns named specialists with their own prompt, description, and (optionally) restricted toolset — useful for **separation of concerns** in a single run: one agent retrieves, another reviews, a third writes.

## Two routing patterns

### `declarative/` — model picks the specialist

You define the agents with crisp `description` strings; the model reads them at every turn and chooses which to spawn based on the task. Same prompt across runs, different delegations depending on what the prompt actually needs. Best for **chat backends** where the caller doesn't know in advance which specialist will be relevant.

```python
motor = Motor(MotorConfig(
    default_agents={
        "code-reviewer": AgentDefinition(description="...", prompt="...", tools=[...]),
        "doc-checker":   AgentDefinition(description="...", prompt="...", tools=[...]),
    },
    default_tools=["Read", "Grep", "Glob", "Agent"],
))

await motor.run(RunTask(prompt="Look at calc.py: docs match? code quality?"))
```

### `explicit/` — caller names the agent in the prompt

Same wiring, but the prompt says *"Use the security-reviewer agent to ..."*. Bypasses the model's automatic routing and guarantees the delegation. Best for **deterministic orchestrators** (compliance pipelines, fixed-order chains) and **regression tests** where you want exactly one delegation path.

## Two enabling moves required (always)

The motor's `default_disallowed_tools` includes `"Agent"` by design — a plain `Motor()` cannot spawn subagents until the dev opts in **explicitly**:

1. `default_agents={...}` (or `RunTask.agents={...}`)
2. `"Agent"` in `default_tools` (or `RunTask.tools`)

If you set just one, `motor.run()` raises a clear `RuntimeError` at validation time — no silent failure, no surprise.

## How the toolsets compose

By default a subagent **inherits** the parent's full toolset (built-in tools + every `@tool` callable the parent has). To **restrict** a subagent, set `tools=[...]` on its `AgentDefinition` — the motor then mounts every callable referenced anywhere in the run on **one shared MCP server** (deduped by name) and rewrites each subagent's tools list to the prefixed `mcp__sophia__<name>` form before forwarding to the SDK.

A `@tool` function can live **only** inside an `AgentDefinition.tools` list (not on the parent) and the motor still finds it. See [`../python-tools/subagent.py`](../python-tools/subagent.py) for the worked example combining `@tool` + subagents.

## Run either example

```bash
pip install sophia-motor
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

python declarative/main.py
python explicit/main.py
```

Each prints the parent's reasoning and the spawned subagent's specialist response, with `[Agent] spawning subagent` log lines as the dispatches happen.

## Caveats verified empirically

- **Subagents do NOT see the parent's `system_prompt`**. Each gets its own `AgentDefinition.prompt` — that's the entire instruction surface. Don't rely on context "leaking down".
- **`Agent` cannot spawn `Agent`** by default. The motor strips Agent from the subagent's inherited toolset to avoid recursion. Override only if you genuinely need nested orchestration.
- **Cost is parent + child tokens**. A run with 2 subagent dispatches costs roughly 2-4× a single-turn run. Worth it when the specialists are cheap on context.

## See also

- [`AgentDefinition`](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py) — re-exported as `from sophia_motor import AgentDefinition`
- [`api_patterns.md`](https://github.com/2sophia/skills/blob/main/skills/sophia-motor/subagents.md) in the skill repo for the deeper reference
