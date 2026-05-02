# subagents — explicit invocation by name

Like the declarative pattern, but the prompt **names the subagent** —
"Use the security-reviewer agent to ..." — to bypass the model's
automatic routing and guarantee the delegation. Useful for:

- Deterministic orchestrators that chain specialists in a fixed order
- Test runs that must reproduce the same delegation
- Prompts where you don't want to rely on the model picking the right
  specialist from the descriptions

```python
motor = Motor(MotorConfig(
    default_agents={"security-reviewer": AgentDefinition(...)},
    default_tools=["Read", "Agent"],   # whitelisting Agent in tools is enough
))

await motor.run(RunTask(
    prompt="Use the security-reviewer agent to audit this snippet: ..."
))
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## Tip — preserve subagent output verbatim

By default the parent agent receives the subagent's final message and
may summarize it in its own response. To keep the subagent's output
intact, instruct the parent in the prompt: "Return the subagent's
findings verbatim." (See the example above.)
