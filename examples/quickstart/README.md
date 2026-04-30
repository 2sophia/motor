# quickstart

The smallest possible sophia-motor program: one prompt, one motor, one
answer. No tools, no schema, no skills. Verifies that your install and
API key are wired up correctly.

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
