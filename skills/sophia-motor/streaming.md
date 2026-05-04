# Streaming — `motor.stream()` + chunk types

`motor.run(task)` collects everything and returns a `RunResult`. `motor.stream(task)` exposes the same execution as a typed async iterator — you render token-by-token, decide when to stop, react to events live. Same source of truth: `run()` is a thin wrapper around `stream()`.

```python
from sophia_motor import (
    Motor, RunTask,
    DoneChunk, ErrorChunk, InitChunk, OutputFileReadyChunk,
    RunStartedChunk, TextDeltaChunk, TextBlockChunk,
    ThinkingDeltaChunk, ThinkingBlockChunk,
    ToolUseStartChunk, ToolUseDeltaChunk, ToolUseCompleteChunk,
    ToolUseFinalizedChunk, ToolResultChunk,
)

motor = Motor()

async def render(task: RunTask):
    async for chunk in motor.stream(task):
        match chunk:
            case TextDeltaChunk():    print(chunk.text, end="", flush=True)
            case ToolUseStartChunk():  print(f"\n[Tool] {chunk.tool}...")
            case ToolResultChunk():    print(f"\n  → {chunk.preview[:80]}")
            case DoneChunk():          return chunk.result    # the RunResult
```

`DoneChunk.result` is always emitted last (terminal), even on error.

## Chunk reference

All chunks inherit `_ChunkBase(BaseModel, extra="forbid")`. The package exports `StreamChunk` as `Annotated[Union[...], Field(discriminator="type")]` for typed handling.

| Chunk | `type` literal | Other fields |
|---|---|---|
| `RunStartedChunk` | `"run_started"` | `run_id: str`, `model: str`, `prompt_preview: str`, `max_turns: int` |
| `InitChunk` | `"init"` | `session_id: str \| None` (one-shot per run, when CLI subprocess reports) |
| `TextDeltaChunk` | `"text_delta"` | `text: str` |
| `TextBlockChunk` | `"text_block"` | `text: str` (fallback when no deltas streamed; **don't concatenate** with prior deltas — it's the entire block) |
| `ThinkingDeltaChunk` | `"thinking_delta"` | `text: str` |
| `ThinkingBlockChunk` | `"thinking_block"` | `text: str` |
| `ToolUseStartChunk` | `"tool_use_start"` | `tool_use_id: str`, `tool: str`, `index: int` |
| `ToolUseDeltaChunk` | `"tool_use_delta"` | `tool_use_id: str`, `tool: str`, `partial_json: str` (raw fragment), `extracted: dict` (best-effort partial parse), `index: int` |
| `ToolUseCompleteChunk` | `"tool_use_complete"` | `tool_use_id: str`, `tool: str` |
| `ToolUseFinalizedChunk` | `"tool_use_finalized"` | `tool_use_id: str`, `tool: str`, `input: dict` (canonical, authoritative) |
| `ToolResultChunk` | `"tool_result"` | `tool_use_id: str`, `is_error: bool`, `preview: str` |
| `OutputFileReadyChunk` | `"output_file_ready"` | `relative_path: str`, `path: str` (**str not Path** for SSE serialization), `tool: str` (`"Write"` or `"Edit"`) |
| `ErrorChunk` | `"error"` | `message: str` (non-fatal during stream; `DoneChunk` follows) |
| `DoneChunk` | `"done"` | `result: RunResult` (terminal, always last) |

## Two ordering quirks worth remembering

### 1. `extracted` is best-effort, `input` is authoritative

`ToolUseDeltaChunk.extracted` is a tolerant parse of the JSON-in-progress. It's perfect for live UI rendering — show the user the tool name + partial args as they stream — but **don't make logic decisions on it**. The canonical args land in `ToolUseFinalizedChunk.input`. Pattern:

```python
case ToolUseDeltaChunk():
    ui.show_partial(chunk.extracted)         # render

case ToolUseFinalizedChunk():
    real_args = chunk.input                  # use this
```

### 2. `ToolUseFinalizedChunk` may arrive **before** `ToolUseCompleteChunk`

SDK 0.1.71 ordering: don't treat `_complete` as the "input is done" signal. Use `_finalized` for the canonical end-of-tool-use. `_complete` exists for symmetry but isn't authoritative on input.

## vLLM upstream caveat

Running against vLLM/Qwen via `VLLMAdapter`, the upstream typically does **not** emit `input_json_delta` chunks. Consequence: `ToolUseDeltaChunk` won't fire. Other chunk types work normally (`ToolUseStartChunk`, `ToolUseFinalizedChunk`, `ToolResultChunk`, `TextDeltaChunk`, `ThinkingDeltaChunk`). UI that depends on partial tool args needs a placeholder for the vLLM path. Documented in [`adapters.md`](adapters.md).

## Backward compat with `EventBus`

Streaming and event bus coexist:

- `motor.stream()` → typed `StreamChunk`s for a single async consumer
- `@motor.on_event` / `@motor.on_log` → fan-out to N subscribers, run continues independently

Use streaming for "one consumer renders the run live" (chat UI, console). Use the event bus for "multiple side-channel observers" (metrics, audit hook, log shipper). They're not exclusive — both fire during every run.

## When to choose `stream()` vs `run()`

| Goal | Use |
|---|---|
| Render token-by-token to a UI / TTY | `stream()` |
| Show progress bars / live tool-use | `stream()` |
| Cancel mid-flight on user signal | `stream()` + `motor.interrupt()` |
| Just get the typed result | `run()` |
| Background / batch processing | `run()` |
| `motor.chat()` / `motor.console()` | already wired to `stream()` internally |

## Live verification

- `examples/streaming/main.py` — minimal token-by-token rendering
- `examples/file-creation/main.py` — `OutputFileReadyChunk` live signal
- `examples/interrupt/main.py` — cancellation mid-stream

## When uncertain

- Chunk shape? `python -c "from sophia_motor import TextDeltaChunk; help(TextDeltaChunk)"`. Each chunk class has its own docstring.
- "Why isn't `ToolUseDeltaChunk` firing?" → Are you on vLLM? Check the chunks you DO see — if `ToolUseStartChunk` + `ToolUseFinalizedChunk` are there but `ToolUseDeltaChunk` isn't, it's the vLLM quirk, not a bug.
- Want to add a custom chunk type? You'd need to fork `_chunks.py` + the `_iter_stream_chunks` dispatcher in `motor.py` (~line 400). Non-trivial; usually better solved with an event subscriber.
