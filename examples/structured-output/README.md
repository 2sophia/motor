# structured-output

Pydantic schema in, Pydantic instance out. The model returns a JSON
object that already conforms to your `BaseModel`: enums, numeric ranges,
string patterns, and nested objects are all enforced server-side before
you ever see the response.

## What this example shows

- A `TicketTriage` schema with five typed fields (enums + bounded float
  + length-constrained string).
- A single `motor.run(RunTask(..., output_schema=TicketTriage))` call.
- Reading `result.output_data` as a typed object and accessing fields
  with full IDE autocomplete and `mypy` support.

## Run

```bash
pip install sophia-motor
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

## What you should see

Five-line structured output (category, priority, sentiment, language,
summary) followed by a metadata footer. The model never returns a
free-form paragraph here — it commits to the contract.
