# Observability — events, logs, audit

The motor exposes everything it does as either typed events on the bus, structured log records, or files on disk. Pick the right shape for your consumer.

## The three streams

| Stream | Shape | When to use |
|---|---|---|
| `Event` | Typed, structured (`type` + `payload` dict) | Programmatic consumers — metrics, log shippers, UI updates |
| `LogRecord` | `level` + `message` + `fields` (free-form text + key/value tags) | Human-readable logs |
| Audit dump | JSON files under `<run>/audit/` | Persistent record, BdI-defensible |

All three coexist. A single tool call emits an `Event(type="tool_use", ...)`, a `LogRecord(level="INFO", message="→ tool_use ...")`, and rows in `request_NNN.json` / `response_NNN.sse`.

## Subscribe to events

```python
@motor.on_event
def on_ev(ev):
    # ev: sophia_motor.Event
    if ev.type == "python_tool_call":
        ...

@motor.on_event
async def on_ev_async(ev):    # async subscribers also work
    await my_metrics.send(ev.payload)
```

Subscribers are sync OR async — the bus auto-detects via `inspect.isawaitable`. Errors raised inside a subscriber are **caught and printed to stderr** — a buggy listener never breaks the motor or other subscribers.

`on_event` can also be used as a direct call (not just decorator):

```python
motor.on_event(my_handler)
motor.on_event(another_handler)
```

## `Event.type` values you'll see

Searched the codebase exhaustively — every `Event(type=...)` emission:

| `type` | Emitter | Payload keys |
|---|---|---|
| `"run_started"` | `motor.py` | `prompt` (200 chars), `model`, `workspace`, `max_turns`, `allowed_tools` |
| `"system_message"` | `motor.py` | `subtype` (e.g. `"init"`, `"status"`) |
| `"assistant_text"` | `motor.py` | `len`, `preview` (200 chars) |
| `"thinking"` | `motor.py` | `len` |
| `"tool_use"` | `motor.py` | `tool`, `input_keys` (list of input dict keys) |
| `"tool_result"` | `motor.py` | `is_error`, `preview` |
| `"result"` | `motor.py` (run end) | `is_error`, `n_turns`, `cost_usd`, `result_preview`, `structured_output_present`, `output_data_validated` |
| `"sdk_message"` | `motor.py` | `kind` (catch-all for unrecognized SDK message classes) |
| `"proxy_request"` | `proxy.py` | `idx`, `model`, `n_messages`, `n_tools`, `stream`, `stripped_blocks`, … |
| `"proxy_response"` | `proxy.py` | `idx`, `status` (sync) / `stream: True` (stream), `stop_reason`, `usage` |
| `"python_tool_call"` | `_python_tools.py` | `name`, `seq`, `duration_ms`, `ok`, `error` |

The README in `examples/events/` lists a curated subset — but the implementation also emits `system_message`, `sdk_message`, and `python_tool_call`. If you don't recognize a `type`, log and ignore.

## `LogRecord` shape

```python
@dataclass
class LogRecord:
    level: Literal["DEBUG","INFO","WARNING","ERROR"]
    message: str
    run_id: str | None = None
    ts: datetime = ...
    fields: dict = ...        # key/value tags
```

```python
@motor.on_log
def on_log(rec):
    if rec.level == "ERROR":
        sentry.capture(rec)
```

Default subscriber `default_console_logger` prints `[HH:MM:SS] LEVEL   message k=v k=v` colored. Enable via `MotorConfig.console_log_enabled=True` (or `SOPHIA_MOTOR_CONSOLE_LOG=true`).

## Default console subscribers

When `console_log_enabled=True` (or env override), the motor pre-registers two subscribers:

- `default_console_logger(record)` → colored line per `LogRecord`
- `default_console_event_logger(event)` → magenta line per `Event`

The console module (`motor.console()`) **detaches** these on entry (so the TUI stays clean) and restores them on exit. If you write your own UI, you'll likely want to do the same (push your handlers, suppress the defaults).

## Audit dump files

```
<run>/
├── input.json                    # task params + config snapshot + manifests
├── trace.json                    # final blocks + metadata
└── audit/
    ├── request_001.json          # body POST /v1/messages (turn 1)
    ├── response_001.sse          # streaming response (or .json for sync)
    ├── request_002.json          # turn 2
    ├── response_002.sse
    ├── ...
    └── tool_<name>_NNN.json      # each @tool invocation, sequenced
```

Honors `MotorConfig.proxy_dump_payloads` (default `False` since 0.4.3 — flip on for dev / audit-required prod):

```python
MotorConfig(proxy_dump_payloads=True)
# or
SOPHIA_MOTOR_AUDIT_DUMP=true python my_app.py
```

The path is `result.audit_dir`. The format is JSON (or SSE for streaming responses), legible from `cat`, `jq`, your audit tool of choice.

## Pattern: clean prod, observable dev

```python
import os

motor = Motor(MotorConfig(
    console_log_enabled=os.getenv("SOPHIA_DEV") == "1",
    proxy_dump_payloads=os.getenv("SOPHIA_DEV") == "1",
))

# Programmatic subscribers — same in dev and prod
@motor.on_event
def to_metrics(ev):
    metrics.counter(f"motor.event.{ev.type}").inc()

@motor.on_log
def to_logs(rec):
    logger.log(rec.level.lower(), rec.message, **rec.fields)
```

Console / audit are debug aids, off by default. Programmatic observability is always on.

## Pattern: observe `python_tool_call` for live tool traces

```python
@motor.on_event
def watch_tools(ev):
    if ev.type == "python_tool_call":
        ok = "✓" if ev.payload["ok"] else "✗"
        print(f"  [tool] {ok} {ev.payload['name']} ({ev.payload['duration_ms']}ms)")
```

Distinct from `Event(type="tool_use")` (which fires for **any** tool, including built-ins). `python_tool_call` is specific to Python `@tool` invocations (with the duration / ok / error info that built-ins don't carry).

## Pattern: cost tracking per call

```python
@motor.on_event
def cost_tracker(ev):
    if ev.type == "result":
        cost = ev.payload.get("cost_usd", 0.0)
        cost_counter.add(cost)
```

Or grab the metadata at the end of `await motor.run(...)`:

```python
result = await motor.run(...)
cost_counter.add(result.metadata.total_cost_usd)
```

## Live verification

`examples/events/main.py` — wires up sync + async subscribers, prints colored events as they arrive.

## When uncertain

- "What event types exist?" → grep `'Event(type='` in the source: `grep -rn "Event(type=" src/sophia_motor/`. The 11 types listed above are the complete set as of 0.5.0.
- "Why isn't my subscriber firing?" → Make sure you registered it on the motor that's running, and that you registered before `motor.run()` was awaited. Subscribers added after a run starts only see *subsequent* events.
- "Can subscribers be removed?" → No public API. They live in `motor.events._event_subs` / `_log_subs`. The `_console.py` REPL pops them by reference; you'd do the same:
  ```python
  motor.events._event_subs.remove(my_handler)
  ```
- "Does the bus persist subscribers across `motor.stop()` / lazy restart?" → Yes. `motor.stop()` shuts down the proxy but doesn't touch the EventBus.
