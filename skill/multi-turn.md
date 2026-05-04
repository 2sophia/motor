# Multi-turn — Chat, Console, Interrupt

Three primitives that turn the motor's stateless `run()` / `stream()` into something interactive. Each has a distinct use case; none are mutually exclusive.

## `Chat` — memory-bearing conversation

```python
chat = motor.chat(chat_id="user-42")
result = await chat.send("Hi, my favorite color is teal.")

# Some time later — same chat_id, same in-memory `chat` object:
result = await chat.send("What's my color?")
print(result.output_text)        # → "teal"
```

Under the hood, `Chat` keeps an SDK `session_id` and a workspace folder shared across turns. Each `send()` resumes the previous session, appends the new turn, returns the `RunResult`.

### Factory

```python
chat = motor.chat(
    chat_id=None,             # str | None — auto-mints chat-<12hex> if None
    session_id=None,          # str | None — pass to resume after restart
    root=None,                # Path | None — defaults to <workspace_root>/../chats/<chat_id>/
)
```

### Public API

```python
chat.chat_id        # str — stable, save to DB
chat.session_id     # str | None — current SDK session, updated every turn
chat.cwd            # Path — shared workspace root, READ-ONLY

await chat.send(prompt_or_task)             # str | RunTask → RunResult
chat.stream(prompt_or_task)                 # str | RunTask → AsyncIterator[StreamChunk]
await chat.reset()                           # "new chat" button — clears session_id, wipes session.jsonl
```

### What `send()` / `stream()` do internally

1. If you pass a `str`, wraps it in `RunTask(prompt=...)`. If you pass a `RunTask`, uses it as-is.
2. Injects `task.session_id = self.session_id` and `task.workspace_dir = self.cwd` if the caller didn't set them. **Caller-set values win** — useful for forking a chat.
3. After the run, picks up `result.metadata.session_id` and stores it on `self.session_id`. The session_id may rotate per turn — re-save it in your DB on every turn if persisting.

### Concurrency rules

- **Different chats** on the same Motor → safe to run in parallel (`asyncio.gather` over `chat_a.send(...)` + `chat_b.send(...)`). Proxy multiplexes by `run_id`.
- **Same chat** → **NOT safe** to interleave concurrent calls. Two `await chat.send(...)` on the same `chat` object racing each other will corrupt the session. Serialize per-chat at the application layer (e.g. an `asyncio.Lock` per chat_id, or a queue).

### Persisting state

```python
# After every send, persist to your DB:
db.save_chat(chat.chat_id, chat.session_id)

# Hours later, restore:
chat = motor.chat(
    chat_id=row.chat_id,
    session_id=row.session_id,
)
```

The CLI's `session.jsonl` lives in `<chat.cwd>/.claude/projects/...` — it persists on disk between Python process restarts. Only the in-memory `Chat` object goes away; the session itself survives.

## `Console` — interactive REPL with live streaming

```python
await motor.console()
```

Opens a chat-like terminal UI:

- live token streaming (rich `Live` panel)
- prompt-toolkit input with multiline + history + Tab completion on `/`-commands
- slash commands: `/help`, `/exit` (`/quit` `/q`), `/files`, `/audit`, `/clear`, `/new`
- `Ctrl-C` mid-run → `motor.interrupt()`, console stays alive
- header panel shows model / upstream / adapter / tools / skills / system / chat_id

Requires the `[console]` extras:

```bash
pip install "sophia-motor[console]"
```

Without it, `motor.console()` raises `ImportError` with the install hint. Lazy import; base `motor` install stays lean (5 deps, no rich/prompt-toolkit).

Internals worth knowing:

- The console mints **one** `Chat` for the whole session (every prompt is a turn on that chat).
- It detaches `default_console_event_logger` and `default_console_logger` from the EventBus while the REPL is open (so the TUI stays clean) and restores them on exit.
- On exit it calls `await motor.stop()`.

Use case: dev quick-test, demo, manual review.

## `motor.interrupt()` — cancel a running run

```python
await motor.interrupt()                          # interrupt whatever is current (must be unambiguous)
await motor.interrupt(run_id="run-1234-...")     # race-safe: only acts if matches
```

### Behaviour

- Returns `bool`: `True` if a matching active run was interrupted, `False` if there was nothing to interrupt (idempotent — never raises in no-active-run case).
- Sets `result.metadata.was_interrupted = True` on the terminal `DoneChunk`.
- `is_error` stays `False` — interrupt is a deliberate user action, not an upstream failure. UI should render "interrupted" distinct from "errored".
- The audit dump up to the cancellation is preserved (`<run>/audit/`).

### Safety / no-arg semantics

- `motor.interrupt()` (no `run_id`) with **zero** active runs → returns `False`, no error.
- With **exactly one** active run → interrupts it.
- With **two or more** active runs → raises `RuntimeError("multiple runs ambiguity")`. Pass `run_id=...` to disambiguate.

### Race-condition safety

```python
# UI flow: user clicks "stop" on run X, but a new run Y started before we got the click
await motor.interrupt(run_id="run-X")    # only kills X; Y keeps running
```

The `run_id` filter is the safer pattern when you have multiple concurrent runs.

### Distinct from `motor.stop()`

| | `motor.stop()` | `motor.interrupt()` |
|---|---|---|
| Scope | Kills the proxy, ends motor lifecycle | Cancels one in-flight run |
| Effect on other runs | All in-flight runs die | They keep running |
| Effect on motor | Motor is unusable until next `await motor.run(...)` (lazy restart) | Motor stays alive |
| Idempotent | No (calling twice on a stopped motor → no-op) | Yes |

## When to use which

| User wants… | Use |
|---|---|
| Multi-turn UI / chat backend | `Chat` |
| Quick TTY exploration | `Console` |
| User-cancellable single run | `motor.run(...)` + `motor.interrupt()` from a sibling task |
| Background batch | Plain `motor.run(...)` |

## Live verification

- `examples/chat/main.py` — `Chat` + persistent state across script restarts
- `examples/console/main.py` — REPL with `[console]` extras
- `examples/interrupt/main.py` — sibling task that cancels mid-stream

## When uncertain

- "Does `Chat` survive across process restarts?" → Yes if you pass back the same `chat_id` + `session_id`. The session.jsonl lives in `<chat.cwd>/.claude/projects/`. As long as that directory survives, the session does.
- "How do I clear a chat without losing the workspace?" → `await chat.reset()`. Same `chat_id` + `cwd`, fresh session_id.
- "Can two chats share the same `cwd`?" → Don't. Two chats writing to the same `.claude/projects/` corrupt each other. Each chat gets its own folder by default.
- Console hangs on `Ctrl-D` → that's `EOF` at the empty prompt, treated as `/exit`. Normal.
