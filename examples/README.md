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
| 2  | [structured-output](./structured-output)     | Pydantic schema in, typed Pydantic instance out (`output_data`).         |
| 3  | [attachments](./attachments)                 | Three input forms — file, directory, inline dict — mixed in one run.     |
| 4  | [skills](./skills)                           | Task-specific instructions + on-the-fly Python execution via skills.    |
| 5  | [events](./events)                           | Hook into every turn the agent takes (`on_event`, `on_log`).             |
| 6  | [concurrency](./concurrency)                 | Fan out N independent runs across N motors with `asyncio.gather`.        |

Every example uses the strict guardrail (the default), so the agent is
sandboxed inside its per-run workspace from the first call.
