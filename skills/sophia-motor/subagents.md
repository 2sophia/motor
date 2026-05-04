# Subagents

Spawn isolated specialist agents from the parent run. Each subagent gets its own conversation context, its own (possibly subset) tool list, optionally a different model, and returns a summary to the parent.

```python
from claude_agent_sdk import AgentDefinition       # re-exported from sophia_motor too
from sophia_motor import Motor, MotorConfig, RunTask
```

## The two-move opt-in (NON-negotiable)

`"Agent"` is in `MotorConfig.default_disallowed_tools` by design. Just declaring `default_agents={...}` does NOT enable subagents — `motor.run()` raises `RuntimeError`. **Two deliberate moves required**:

1. Declare your agents — `MotorConfig.default_agents={"name": AgentDefinition(...)}` (or per-task `RunTask.agents={...}`).
2. Whitelist `"Agent"` in `tools` — `MotorConfig.default_tools=["Agent", ...]` (or per-task `tools=[..., "Agent"]`).

The motor's conflict-resolution **automatically** removes `"Agent"` from the resolved disallowed list when it sees Agent whitelisted in tools. **Don't** wipe `default_disallowed_tools=[]` to "make Agent allowed" — you'd unblock 17+ tools you didn't intend to.

```python
motor = Motor(MotorConfig(
    default_agents={
        "code-reviewer": AgentDefinition(
            description="Quality, security, and style reviewer.",
            prompt="You are a senior reviewer. List concrete improvements.",
            tools=["Read", "Grep", "Glob"],
        ),
    },
    default_tools=["Read", "Grep", "Glob", "Agent"],     # <-- the second move
))

await motor.run(RunTask(prompt="Review src/auth/"))
```

Without `"Agent"` in `default_tools`, the motor refuses with a clear error message that names exactly what's missing.

## `AgentDefinition` reference

```python
@dataclass
class AgentDefinition:
    description: str                            # the model reads this to decide WHEN to use this agent
    prompt: str                                 # system-prompt for the subagent's context
    tools: list[str] | None = None              # see "Tools across parent + subagents" below
    disallowedTools: list[str] | None = None
    model: str | None = None                    # alias ("sonnet", "opus", "haiku") or full model id
    skills: list[str] | None = None
    memory: Literal["user","project","local"] | None = None
    mcpServers: list[str | dict] | None = None  # in our world, almost always None — see below
    initialPrompt: str | None = None
    maxTurns: int | None = None
    background: bool | None = None
    effort: Literal["low","medium","high","max"] | int | None = None
    permissionMode: PermissionMode | None = None
```

For most users only `description`, `prompt`, `tools` (sometimes), and `model` (sometimes) matter.

## Tools across parent + subagents — three patterns

### A. Inheritance — `tools=None` on the AgentDefinition

```python
AgentDefinition(
    description="...",
    prompt="...",
    # tools NOT specified → subagent inherits the parent's full toolset.
)
```

The subagent sees everything the parent saw, **except `Agent` itself** (the SDK trims `Agent` from sub-contexts to avoid recursion). This is the simplest pattern: declare your tools on the parent, all subagents inherit.

### B. Explicit subset — `tools=[built-in names + @tool callables]`

```python
@tool
async def write_report(args: ReportInput, ctx: ToolContext) -> str: ...

AgentDefinition(
    description="...",
    prompt="...",
    tools=["Read", write_report],     # subagent ONLY sees these
)
```

When you want to **restrict** a specialist to a subset of capabilities (and the parent has more). The motor splits the list into strings (built-in names) and callables, validates, and rewrites callables to `mcp__sophia__<name>` before forwarding to the SDK.

A callable referenced in `AgentDefinition.tools` but NOT on the parent is **still mounted** on the shared MCP server — the subagent can have private tools the parent can't see. This is the canonical "delegate to writer" / "delegate to reviewer" pattern.

### C. Pre-prefixed strings — `tools=["mcp__sophia__name"]`

If you already know the prefixed name (e.g. you're echoing a value back from a previous run's audit), passing the string verbatim works. The motor passes strings through unchanged. Useful for migrating off the SDK's raw API or for dynamic dispatch.

## Inheritance + restriction in one example

```python
@tool
async def fetch_customer(args: ...) -> ...: ...

@tool
async def hash_payload(args: ...) -> ...: ...

@tool
async def write_report(args: ..., ctx: ToolContext) -> str: ...

motor = Motor(MotorConfig(
    default_tools=[fetch_customer, hash_payload, "Agent"],   # parent has 2 tools + Agent
    default_agents={
        "lookup": AgentDefinition(
            description="Looks up users + computes hashes.",
            prompt="...",
            # tools=None → inherits parent → sees fetch_customer + hash_payload
        ),
        "writer": AgentDefinition(
            description="Persists reports to disk.",
            prompt="...",
            tools=[write_report],     # explicit restrict → ONLY write_report
        ),
    },
))
```

What ends up on the wire:

- Parent agent sees: `["Agent", "mcp__sophia__fetch_customer", "mcp__sophia__hash_payload"]`
- `lookup` subagent sees: `["mcp__sophia__fetch_customer", "mcp__sophia__hash_payload"]` (inherited, Agent stripped)
- `writer` subagent sees: `["mcp__sophia__write_report"]` (explicit subset only)
- One shared MCP server `sophia` with all three callables mounted (dedup by name; `write_report` lives there even though the parent can't reach it).

## Two invocation patterns

### Declarative — model picks based on `description`

```python
await motor.run(RunTask(prompt="Review the auth module."))
# Model reads: code-reviewer.description matches → spawns code-reviewer.
```

The model auto-routes based on the prompt + each agent's `description`. Works well when descriptions are distinct and action-oriented.

### Explicit — prompt names the subagent

```python
await motor.run(RunTask(prompt="Use the code-reviewer agent on src/auth.py."))
```

More deterministic. Use when the parent prompt is constructed by your code (programmatic dispatch).

By default the parent **summarises** the subagent's response in its own reply. If you want the subagent's verbatim output, instruct the parent in the prompt: *"Return the subagent's findings verbatim."*

## Bundled-builtins suppression

The Claude CLI ships 4 built-in subagents (`Explore`, `general-purpose`, `Plan`, `statusline-setup`) that the model would otherwise see alongside yours. The motor sets `CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1` in the subprocess env, so the model **only** sees what you declared. Empirically verified — without this flag the model often picks `general-purpose` over your custom agents.

To re-enable the bundled set (rare): not directly exposed; see `motor.py:_build_sdk_options` for the env var if you really need it.

## Token cost

Each subagent invocation is **a fresh conversation** with its own system prompt + tools description. Three subagents in parallel ≈ three conversations worth of input tokens. Break-even vs inline reads is around 4-5 file reads inside the subagent — below that, do it inline; above it, the context isolation pays back.

## Security inside subagents

Subagents inherit the same guardrail mode (`MotorConfig.guardrail`) as the parent. They run in the same workspace cwd. Their tool calls go through the same proxy and land in the same audit dump (`<run>/audit/request_NNN.json` show subagent dispatches as `Agent` tool calls).

## Live verification

- `examples/subagents/declarative/main.py` — model auto-routes by description
- `examples/subagents/explicit/main.py` — prompt names the agent
- `examples/python-tools/subagent.py` — Pattern B (inheritance + explicit-restrict in one run)

## When uncertain

- "Why does the run keep raising `RuntimeError: Subagents require the Agent tool to be reachable...`?" → You forgot move #2 (whitelist `"Agent"` in tools). The error message tells you exactly what to add.
- "The subagent says it can't find tool X." → Either: (a) X isn't on the parent's `tools` AND `AgentDefinition.tools=None` (inheritance), so subagent didn't inherit it, OR (b) `AgentDefinition.tools` doesn't list X. Read what's actually mounted: check the run's `request_001.json` for the parent and `request_NNN.json` for the subagent dispatch — the `tools` array is the ground truth.
- "Can a subagent spawn its own subagents?" → Technically yes (the SDK supports nesting), but the motor strips `Agent` from sub-contexts by default. Re-add `"Agent"` explicitly to a subagent's `tools=` if you really want recursion. **Be careful with depth limits + cost.**
