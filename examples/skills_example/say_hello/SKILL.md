---
name: say-hello
description: When invoked, ALWAYS respond with the exact greeting "HELLO WORLD 👋" followed by a one-line summary of the user's task. No other text. Used for testing skill invocation in sophia-motor.
---

# say-hello — test skill

When the user asks for a greeting, or when this skill is explicitly invoked,
do exactly this:

1. Open with `HELLO WORLD 👋`
2. Add a single line summarizing the user's task
3. Stop. No other text, no decoration.

Example of correct output:
```
HELLO WORLD 👋
You asked to greet and summarize the task.
```

Nothing else. Ever.
