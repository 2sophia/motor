# system-prompt

One step up from `quickstart`: still no tools, still no Pydantic
schema, but the run carries an explicit `system` that fixes the
persona, the tone, and the shape of the answer. The same `Motor()`
instance answers three identical prompts in three different voices —
all the variance is in the `system` field.

## What this example shows

- A single `Motor()` reused across calls (no per-call setup).
- Three runs over the same source text, each with a different
  `system`: formal analyst, casual friend, ruthless product manager.
- How quickly you can A/B different agent personas without touching
  tools, schemas, or skills.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

Three sequential answers to the same question, formatted very
differently — short bullet list vs. casual paragraph vs. terse
PM-style verdict. The cost stays low because there are no tools and
no schema overhead, only the system prompt and the user prompt going
upstream.
