# web-search

The agent searches the live web, fetches a couple of pages, and returns
a typed brief with citations.

`WebSearch` and `WebFetch` are blocked by `default_disallowed_tools` —
listing them explicitly in `tools=[...]` flips them on for this single
run while every other web/agentic tool stays blocked.

## Minimal example

```python
result = await motor.run(RunTask(
    prompt="What's new in Python 3.13? Cite sources.",
    tools=["WebSearch", "WebFetch"],   # opt-in — blocked by default
    output_schema=WebBrief,
))
brief: WebBrief = result.output_data
for src in brief.sources:
    print(src.url, "—", src.takeaway)
```

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

A short web brief printed to stdout — topic, 2–4 sources with title +
URL + one-line takeaway, and a short summary — followed by a metadata
line (turns, tools, cost, duration). The agent typically calls
`WebSearch` once, then `WebFetch` two to four times.

## When to opt in

Most motor runs should leave web access off. Turn it on only when the
task genuinely needs fresh information you can't ship as attachments —
e.g. live release notes, news, current docs. Once on, the agent can
follow links anywhere on the public internet, so prefer this for
internal tools and trusted prompts.
