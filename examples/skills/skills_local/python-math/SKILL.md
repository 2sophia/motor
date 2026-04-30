---
name: python-math
description: Use this skill when the user asks for any numeric calculation, arithmetic, or simple data math (sums, averages, percentages, growth rates, ratios). Compute the answer by running Python inline via `python -c`, never by guessing.
---

# python-math — compute, don't guess

When you receive a math request, compute the answer by running Python
inline via the Bash tool. Do not try to reason out arithmetic in your
head — even a small chain of multiplications can drift.

## Workflow

1. Identify the precise expression that answers the user's question.
2. Call the Bash tool with a `python -c "..."` command that prints the
   result. Use `print(...)` so the value reaches stdout.
3. Read the stdout from the tool result.
4. Reply with: the computed value first, then a one-line explanation
   of what was computed.

## Examples

User: "What's 17.5% of 9,432?"
You run: `python -c "print(round(0.175 * 9432, 2))"`
You report: "1650.6 — that's 17.5% of 9432."

User: "If revenue grew from 240k to 318k, what's the growth rate?"
You run: `python -c "print(round((318 - 240) / 240 * 100, 2))"`
You report: "32.5% — growth from 240 to 318."

## Rules

- Always use `python -c`, never an interactive shell.
- Always `print()` the result.
- Round sensibly: 2 decimal places for percentages, 4 for currency, 0
  for counts.
- Never invent numbers; only compute from values the user gave you.
