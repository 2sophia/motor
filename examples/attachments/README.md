# attachments

Show the three forms the motor accepts as input data, mixed in a single
list:

1. **Real FILE on disk** → symlinked into the run sandbox under
   `attachments/<filename>`.
2. **Real DIRECTORY on disk** → symlinked into `attachments/<dirname>/`.
3. **Inline `dict[str, str]`** → contents are written to disk for you,
   useful for fixtures, tests, and tiny configs you don't want in a
   separate file.

## Why symlinks?

By default Path-based attachments are linked, not copied. This costs
zero extra storage even on multi-gigabyte directories. The audit
defense is independent of the filesystem state — what the model
*actually read* is dumped verbatim in `<run>/audit/`.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

A summary of the (synthetic) project, a list of errors extracted from
the inline log files, and the configured regions read from the JSON
config — all from one prompt against three different attachment forms.
