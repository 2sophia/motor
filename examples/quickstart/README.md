# quickstart

The smallest possible sophia-motor program: one prompt, one motor, one
answer. No tools, no schema, no skills. Verifies that your install and
API key are wired up correctly.

## Minimal example

```python
from sophia_motor import Motor, RunTask

motor = Motor()  # reads ANTHROPIC_API_KEY from env or ./.env

result = await motor.run(RunTask(
    prompt="Explain in two sentences what makes a good API design.",
))
print(result.output_text)
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

A two-sentence answer printed to stdout, followed by a single line of
metadata (turns, tokens, cost, duration). Total cost is typically a few
tenths of a cent.
