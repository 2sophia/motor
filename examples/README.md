# sophia-motor examples

Each subfolder is a self-contained, copy-paste-ready example. Install
the package once, then `cd` into any folder and run `python main.py`.

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
cd examples/quickstart
python main.py
```

## Suggested reading order

| #  | Folder                                       | What it teaches                                                          |
|----|----------------------------------------------|--------------------------------------------------------------------------|
| 1  | [quickstart](./quickstart)                   | The smallest possible run: prompt → answer.                              |
| 2  | [system-prompt](./system-prompt)             | Same prompt, three personas — `system` is the cheapest knob.             |
| 3  | [structured-output](./structured-output)     | Pydantic schema in, typed Pydantic instance out (`output_data`).         |
| 4  | [attachments](./attachments)                 | Hand the agent a folder of real files — Glob + Read on hard-links.      |
| 5  | [file-creation](./file-creation)             | The agent writes files — `Write`/`Edit`, `result.output_files`, persist. |
| 6  | [skills](./skills)                           | Task-specific instructions, inline Python, bundled helper scripts.       |
| 7  | [web-search](./web-search)                   | Live internet — `WebSearch` + `WebFetch`, typed brief with citations.    |
| 8  | [events](./events)                           | Hook into every turn the agent takes (`on_event`, `on_log`).             |
| 9  | [streaming](./streaming)                     | Render output token-by-token — `motor.stream(task)` typed chunks.        |
| 10 | [interrupt](./interrupt)                     | Cancel an in-flight run — `motor.interrupt()` + `was_interrupted` flag.  |
| 11 | [concurrency](./concurrency)                 | One motor, N runs in parallel via `asyncio.gather` — chat-backend pattern. |

Every example uses the strict guardrail (the default), so the agent is
sandboxed inside its per-run workspace from the first call.
