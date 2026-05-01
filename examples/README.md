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
| 5  | [skills](./skills)                           | Task-specific instructions, inline Python, bundled helper scripts.       |
| 6  | [web-search](./web-search)                   | Live internet — `WebSearch` + `WebFetch`, typed brief with citations.    |
| 7  | [events](./events)                           | Hook into every turn the agent takes (`on_event`, `on_log`).             |
| 8  | [streaming](./streaming)                     | Render output token-by-token — `motor.stream(task)` typed chunks.        |
| 9  | [concurrency](./concurrency)                 | Fan out N independent runs across N motors with `asyncio.gather`.        |

Every example uses the strict guardrail (the default), so the agent is
sandboxed inside its per-run workspace from the first call.
