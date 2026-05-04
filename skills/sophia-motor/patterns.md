# Patterns — singleton motor, concurrency, chat backend, batch verdict

The recipes that make `sophia-motor` apps clean. Read the [`core-api.md`](core-api.md) golden rules first, then come here for assembled patterns.

## 1. Singleton motor (the canonical pattern)

```python
# motor.py — single source of truth
from typing import Literal
from pydantic import BaseModel
from sophia_motor import Motor, MotorConfig, tool

class CustomerLookup(BaseModel):
    customer_id: str

class Customer(BaseModel):
    name: str
    tier: str

@tool
async def fetch_customer(args: CustomerLookup) -> Customer:
    """..."""
    ...

class Verdict(BaseModel):
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    rationale: str

motor = Motor(MotorConfig(
    default_system="You are a compliance assistant.",
    default_tools=[fetch_customer, "Read", "Glob"],
    default_output_schema=Verdict,
    default_max_turns=15,
))
```

```python
# functions.py — domain operations call the singleton
from .motor import motor, Verdict
from sophia_motor import RunTask

async def assess_customer(customer_id: str, controls: list[str]) -> Verdict:
    """One reusable smart function. Reuses the motor's defaults."""
    result = await motor.run(RunTask(prompt=(
        f"Assess customer {customer_id} against:\n" + "\n".join(controls)
    )))
    if result.metadata.is_error:
        raise RuntimeError(result.metadata.error_reason)
    return result.output_data
```

```python
# main.py / fastapi.py / cli.py — wherever
from .functions import assess_customer

# Endpoint:
@app.post("/assess")
async def endpoint(req: Request):
    v = await assess_customer(req.customer_id, req.controls)
    return v.model_dump()

# Script:
async def main():
    v = await assess_customer("ACME-001", ["KYC", "AML"])
asyncio.run(main())
```

Why this is the right shape:
- **One motor**, lazily started, reused everywhere → one proxy, one event bus, one place to wire observability
- **Defaults on the motor**, overrides in `RunTask` → boilerplate-free call sites
- **Smart function = `async def` Python** wrapping `motor.run()` → no special framework, just functions

## 2. Concurrency on one motor

```python
import asyncio

results = await asyncio.gather(*[
    motor.run(RunTask(prompt=q)) for q in queries
])
```

The proxy multiplexes by `run_id`. No lock. Any number of concurrent runs.

When to use **multiple** motor instances instead:

- Different `upstream_base_url` / `upstream_adapter` (e.g. one motor for Anthropic, one for vLLM)
- Different `workspace_root` (separate audit trails per program)
- Different `guardrail` (one strict for user-facing, one permissive for trusted internal tasks)

Concurrency alone is **not** a reason to spawn motors.

## 3. Chat backend (N concurrent users)

```python
from sophia_motor import Motor, MotorConfig
from .db import save_chat_session, get_chat_session

motor = Motor(MotorConfig(...))

async def handle_user_message(user_id: str, message: str) -> str:
    row = await get_chat_session(user_id)
    chat = motor.chat(
        chat_id=row.chat_id if row else None,
        session_id=row.session_id if row else None,
    )
    result = await chat.send(message)
    await save_chat_session(user_id, chat.chat_id, chat.session_id)
    return result.output_text
```

Per-user concurrency is parallel by default; **per-user serial** within one chat: the same `chat` object can't have two `await chat.send()` interleaved. Lock per `chat_id`:

```python
import asyncio
_chat_locks: dict[str, asyncio.Lock] = {}

async def handle(user_id, msg):
    lock = _chat_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        ...
```

This is the sophia-agent pattern, the one the maintainers ship in production. See [`multi-turn.md`](multi-turn.md) for the full Chat reference.

## 4. Batch verdict pipeline (RGCI-shaped)

```python
async def assess_obligation(obligation: str, controls: list[str]) -> Verdict:
    return (await motor.run(RunTask(
        prompt=f"Obligation: {obligation}\nControls: {controls}"
    ))).output_data

async def batch_assess(items: list[Item]) -> list[Verdict]:
    semaphore = asyncio.Semaphore(8)        # cap concurrency to avoid rate-limit
    async def with_limit(it):
        async with semaphore:
            return await assess_obligation(it.text, it.controls)
    return await asyncio.gather(*[with_limit(it) for it in items])
```

`Semaphore` rather than unlimited `gather` — Anthropic rate-limits, motor doesn't (yet) carry a budget circuit-breaker. Cost tracking is your responsibility:

```python
@motor.on_event
def cost_track(ev):
    if ev.type == "result":
        budget.add(ev.payload["cost_usd"])
        if budget.total_usd > MAX_BATCH:
            raise RuntimeError("budget exceeded")    # subscribers can't actually abort the run; this just logs
```

A real circuit-breaker (abort the run mid-flight on budget exceeded) is on the roadmap — see `gap_real_use.md` Tier 1.

## 5. Streaming UI (chat-like rendering)

```python
async def render_to_socket(socket, task: RunTask):
    async for chunk in motor.stream(task):
        match chunk:
            case TextDeltaChunk():
                await socket.send({"type": "text", "data": chunk.text})
            case ToolUseStartChunk():
                await socket.send({"type": "tool", "name": chunk.tool})
            case ToolResultChunk():
                await socket.send({"type": "tool_done", "preview": chunk.preview})
            case OutputFileReadyChunk():
                await socket.send({"type": "file", "path": chunk.relative_path})
            case DoneChunk():
                await socket.send({"type": "done", "cost": chunk.result.metadata.total_cost_usd})
```

Pair with `motor.interrupt(run_id=...)` from a sibling task triggered by a stop button.

## 6. Skills + helper scripts (proprietary logic hidden from the model)

```
project/
├── motor.py
└── skills/
    └── compute-discount/
        ├── SKILL.md         # tells the agent: "When the user mentions discount, run python ${SKILL_DIR}/scripts/discount.py"
        └── scripts/
            └── discount.py  # the proprietary tier table, NOT visible to the model
```

```python
motor = Motor(MotorConfig(
    default_skills=Path("./skills/"),
    default_tools=["Skill", "Bash"],     # Skill to invoke, Bash to run helpers
))
```

The model never sees the discount percentages — they're inside the script. Audit trail records every invocation. See [`skills.md`](skills.md).

## 7. Per-program subagent specialization

```python
motor = Motor(MotorConfig(
    default_agents={
        "compliance-officer": AgentDefinition(
            description="Reviews obligations against control matrices.",
            prompt="You are a compliance officer. ...",
            tools=["Read", "Grep", search_obligations],
            model="opus",
        ),
        "evidence-finder": AgentDefinition(
            description="Searches the document store for evidence backing a claim.",
            prompt="You are an evidence specialist. ...",
            tools=[search_docs, fetch_paragraph],
            model="haiku",   # cheaper for retrieval
        ),
    },
    default_tools=["Agent"],
))
```

The parent dispatcher uses just `Agent` + chooses specialists. Each specialist has the minimum tools it needs. Different models per specialist optimize cost.

## 8. Dev/prod toggle

```python
import os
DEV = os.getenv("SOPHIA_DEV") == "1"

motor = Motor(MotorConfig(
    console_log_enabled=DEV,
    proxy_dump_payloads=DEV,
    guardrail="strict" if not DEV else "permissive",
))
```

Or via env (no code change):

```bash
# dev
SOPHIA_MOTOR_CONSOLE_LOG=true SOPHIA_MOTOR_AUDIT_DUMP=true python my_app.py

# prod
python my_app.py
```

## 9. Workspace cleanup at startup

```python
motor = Motor(MotorConfig(...))
removed = motor.clean_runs(older_than_days=7)    # housekeeping at boot
log.info(f"cleaned up {len(removed)} stale runs")
```

Or per N runs to bound disk usage:

```python
motor.clean_runs(keep_last=100)
```

## 10. Composing two motors

When you need different upstreams:

```python
anthropic_motor = Motor(MotorConfig(upstream_adapter="anthropic"))
vllm_motor      = Motor(MotorConfig(
    upstream_base_url="http://vllm:8000",
    upstream_adapter="vllm",
    workspace_root=Path("/data/vllm-runs"),    # separate audit trail
))

# Route based on cost / latency / privacy
async def route(task: RunTask):
    if task.prompt and "internal" in task.prompt.lower():
        return await vllm_motor.run(task)
    return await anthropic_motor.run(task)
```

Two proxies, two ports, two workspaces. They don't interfere.

## When uncertain

- "Should I use Chat or just session_id on RunTask?" → Use `Chat`. The session_id-on-task is internal plumbing; `Chat` does the workspace + session.jsonl management for you.
- "Is it safe to call motor.run from inside a `@tool`?" → Today: no `ctx.motor` back-ref is exposed. The pattern is "the model orchestrates" — let it call N tools across turns rather than nest motor calls.
- "Should I one motor or N motors per worker?" → If they share config: one. If they have different upstream/workspace/guardrail: N. Per-worker thread doesn't matter — the motor is async-ready.
