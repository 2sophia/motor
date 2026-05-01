# chat

Multi-turn dialog — the agent **remembers prior turns**. This is the
pattern you'd use to build a chat backend (sophia-agent style): one
Motor, many concurrent users, each with their own persistent
conversation.

## Minimal example — 4 lines

```python
import asyncio
from sophia_motor import Motor

motor = Motor()
chat = motor.chat()                            # nuovo chat
await chat.send("My favorite city is Lisbon.")
r = await chat.send("Recommend two restaurants there.")  # ← sa Lisbon
print(r.output_text)
```

That's it. `chat.send(prompt)` returns a `RunResult` like `motor.run()`,
but every call **continues the same SDK session**, so the agent has
the prior turns in context.

## How it works under the hood

- `motor.chat(chat_id=...)` mints / opens a chat and a shared workspace
  under `~/.sophia-motor/chats/<chat_id>/`.
- The first `send()` runs against the SDK without a `resume`, captures
  the new `session_id` from the CLI's init message, stores it on the
  Chat instance.
- The next `send()` passes that `session_id` back via
  `RunTask.session_id` → the SDK CLI fetches `session.jsonl` from
  `~/.claude/projects/<encoded-cwd>/<session>.jsonl` and replays the
  prior conversation history before processing the new prompt.
- `chat.session_id` is a plain string. Save it (DB row, JSON file,
  Redis, …) and pass it back to `motor.chat(chat_id=..., session_id=...)`
  to resume after a process restart.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/chat
python main.py
```

The example demos four patterns:

1. **Smallest possible dialog** — 4 lines, two `send()` calls with memory.
2. **Multi-user backend** — one Motor, dict of Chat per user, parallel
   conversations with isolated workspaces. The pattern your FastAPI /
   aiohttp endpoint would call.
3. **Persist + resume** — save `(chat_id, session_id)` to disk, restart
   the process, resume the dialog. The first run establishes the chat
   and writes `chat-state.json`; the second run reads it and asks a
   question whose answer depends on the first run's memory.
4. **`chat.reset()`** — "new chat" button: keeps the workspace, drops
   the SDK session, fresh memory.

## The chat API

| Method / attr | What |
|---|---|
| `await chat.send(prompt: str)` | Run a turn; returns `RunResult`. Updates `chat.session_id`. |
| `await chat.send(task: RunTask)` | Same, but you can override per-turn (different tools for one message, etc.). |
| `chat.stream(prompt)` | Async iterator of `StreamChunk` — same as `motor.stream()` but with chat memory. |
| `await chat.reset()` | New SDK session, same `chat_id` + `cwd`. Drops `~/.claude/projects/<cwd>/`. |
| `chat.chat_id` | str — stable identifier you control (or auto-minted). Save in your DB. |
| `chat.session_id` | str \| None — current SDK session. Save in your DB to resume. |
| `chat.cwd` | Path — shared workspace, read-only. |

## Multi-user backend pattern

Pretty much identical to `agent_service.send_message(chat_id, prompt)`
in sophia-agent:

```python
from fastapi import FastAPI
from sophia_motor import Motor, MotorConfig

motor = Motor(MotorConfig(default_tools=["Read"]))
chats = {}                                      # chat_id -> Chat

app = FastAPI()

@app.post("/messages")
async def send_message(chat_id: str, prompt: str):
    if chat_id not in chats:
        # In production you'd hydrate chat_id + session_id from DB:
        #   row = db.get_chat(chat_id)
        #   chats[chat_id] = motor.chat(chat_id=chat_id,
        #                               session_id=row.session_id)
        chats[chat_id] = motor.chat(chat_id=chat_id)
    reply = await chats[chat_id].send(prompt)
    # Persist the (possibly updated) session_id
    db.update_session_id(chat_id, chats[chat_id].session_id)
    return {"text": reply.output_text}
```

Concurrent requests for **different** chats run in parallel on the
same Motor (the proxy multiplexes by run_id). Concurrent requests for
**the same** chat are not safe to interleave — serialize them per
chat_id at your application layer (queue, lock per chat, whatever
your framework gives you).

## Streaming for a chat UI

```python
async for chunk in chat.stream("write a haiku about grappa"):
    if isinstance(chunk, TextDeltaChunk):
        await ws.send_text(chunk.text)         # render live in browser
```

Same `StreamChunk` types as `motor.stream()`. The only difference is
that `chat.stream()` injects `session_id` + `workspace_dir` so the
chat continues; the chunks themselves are identical.
