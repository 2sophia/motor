# skills

Load a folder of skills and let the agent pick the right one. Skills
are how you give the model **task-specific operating instructions**
that you want followed precisely — not general capability.

## Minimal example

```python
from pathlib import Path
from sophia_motor import Motor, RunTask

motor = Motor()

result = await motor.run(RunTask(
    prompt="Apply the gold-tier discount to a $1500 order.",
    tools=["Skill", "Bash"],            # Skill exposes the catalogue
    skills=Path("./skills_local/"),     # folder with one subdir per skill
))
print(result.output_text)
```

## What this example shows

Three skills are bundled in `skills_local/`:

| Skill            | What it does                                                                  |
|------------------|-------------------------------------------------------------------------------|
| `say-hello`      | Minimal instructional skill — replies with a fixed format                     |
| `python-math`    | Computes arithmetic via inline `python -c` (CAGR, percentages)                |
| `apply-discount` | Bundles a helper script with a proprietary discount table, executed via Bash  |

`python-math` shows the **inline Python** pattern: the skill instructs
the agent to run a one-off `python -c "..."` for any calculation. No
helper file, just stdlib on the host.

`apply-discount` shows the **bundled-helper-script** pattern: the
skill ships its own Python file (`scripts/discount.py`) containing
proprietary logic the agent cannot guess. The agent invokes it via
Bash with `python "$CLAUDE_CONFIG_DIR/skills/apply-discount/scripts/discount.py" <TIER> <AMOUNT>`
and consumes the JSON output. This is how you give an agent
domain-specific behaviour that must be deterministic — pricing,
scoring, anonymization, lookup tables — without exposing the
implementation in the prompt.

## How skill linking works

`MotorConfig.default_skills = SKILLS_DIR` (or `RunTask.skills = ...`)
points the motor at a folder containing one subdirectory per skill.
On each run the motor symlinks every subdirectory that holds a
`SKILL.md` under `<run>/.claude/skills/<name>/`. Use
`disallowed_skills=[...]` to opt specific skills out of a run.

You can also pass a list of folders (e.g. one per program plus an
org-shared one). Name conflicts across folders raise a clear
`ValueError`.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

Three sequential runs, one per skill, each with a clearly-formatted
answer and a short metadata line. The `apply-discount` run will make
a Bash tool call to invoke the bundled `discount.py` script — you can
verify it in the audit dump under
`<workspace>/<run>/audit/response_001.sse`.
