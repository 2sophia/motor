# streaming

Render the agent's output **token-by-token** instead of waiting for the
final answer. Same task as the other examples, but you watch the model
think, open a tool, fill in the input, get the result, and write the
answer — all live.

## Minimal example

```python
async for chunk in motor.stream(task):
    if isinstance(chunk, TextDeltaChunk):
        print(chunk.text, end="", flush=True)
    elif isinstance(chunk, ToolUseStartChunk):
        print(f"\n[{chunk.tool} …]")
    elif isinstance(chunk, DoneChunk):
        return chunk.result   # final RunResult, same as motor.run()
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/streaming
python main.py
```

## Two ways to consume a run

`sophia-motor` gives you two APIs over the exact same execution:

| API | When to use it | What you get |
|---|---|---|
| `await motor.run(task)` | You only care about the final answer (script, batch job, structured output) | One `RunResult` at the end |
| `async for chunk in motor.stream(task)` | You want to render live (chat UI, terminal narrator, progress feedback) | A typed sequence of `StreamChunk` ending with `DoneChunk` |

`run()` is internally a thin wrapper around `stream()`, so you never lose
information by choosing one over the other — `stream()` always ends with
a `DoneChunk` carrying the same `RunResult` that `run()` would return.

## The chunk types

The stream is a discriminated union on `chunk.type`. Match with `isinstance`
or `chunk.type == "..."`.

| Chunk | When | Use it for |
|---|---|---|
| `RunStartedChunk` | First chunk, always | Show "run started", capture `run_id` for logs/audit |
| `InitChunk` | Once, when CLI announces session | Capture `session_id` |
| `ThinkingDeltaChunk` | Chunk-by-chunk reasoning | Render a live "thinking…" panel |
| `ThinkingBlockChunk` | Fallback if deltas didn't stream | Same panel, but you got the whole block at once |
| `ToolUseStartChunk` | Model opens a tool call | Show `[Read …]` placeholder |
| `ToolUseDeltaChunk` | Tool input streams in | Live preview of `file_path`, `command`, etc. via `chunk.extracted` |
| `ToolUseFinalizedChunk` | Authoritative tool input | Replace the live preview with the canonical args |
| `ToolUseCompleteChunk` | Content block closed | Often a no-op for UI (finalized already painted) |
| `ToolResultChunk` | Tool returned | Show ✓/✗ + a snippet of the result |
| `TextDeltaChunk` | Answer streams in token-by-token | Append to the chat bubble |
| `TextBlockChunk` | Fallback if deltas didn't stream | Same bubble, full block at once |
| `ErrorChunk` | Non-fatal error | Surface the message; a `DoneChunk` still follows |
| `DoneChunk` | Last chunk, always | Final `RunResult` (output, metadata, audit dir) |

## Why `ToolUseDeltaChunk.extracted` matters

The Anthropic streaming API sends a tool's JSON input as a sequence of
`input_json_delta` fragments. Mid-stream, the buffer might look like:

```
{"file_path": "outputs/repor
```

That's not valid JSON. `sophia-motor` runs a tolerant parser per delta
and gives you `chunk.extracted = {"file_path": "outputs/repor"}` — useful
to render a live filename/command in the UI before the model has finished
sending the call. When the call commits, `ToolUseFinalizedChunk.input`
gives you the authoritative dict (don't trust `extracted` for logic).

## Ordering note

The Claude Agent SDK 0.1.71 may emit `ToolUseFinalizedChunk` **before**
`ToolUseCompleteChunk` for a given tool. Don't rely on `_complete` as
the "tool finished sending input" signal — use `_finalized`. This is an
SDK ordering quirk, not a `sophia-motor` decision.
