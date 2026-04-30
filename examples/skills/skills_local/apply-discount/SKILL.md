---
name: apply-discount
description: Use this skill to apply a tier-based customer discount. The discount percentages are proprietary and live in the bundled helper script — never guess them. Always invoke the script via Bash.
---

# apply-discount — proprietary discount table

This skill ships its own helper at `scripts/discount.py`. The discount
percentage for each customer tier is defined inside that script and is
**not visible to you**. You must run the helper to get the correct
final amount — any "estimate" you produce on your own will be wrong.

## Workflow

1. Read the `tier` and `amount` from the user's request.
2. Invoke the helper through the Bash tool. The skill folder is exposed
   via the `CLAUDE_CONFIG_DIR` environment variable, so the absolute
   command is:

   ```bash
   python "$CLAUDE_CONFIG_DIR/skills/apply-discount/scripts/discount.py" <TIER> <AMOUNT>
   ```

3. The script prints a JSON object on stdout with `tier`,
   `original_amount`, `discount_pct`, `discount_amount`, and
   `final_amount`.
4. Reply with a one-line summary first ("`<tier>` tier · −X% · final
   $Y.YY"), then the raw JSON for programmatic consumers.

## Example

User: "Apply the tier discount for a SILVER customer with a $480 cart."

You run:
```bash
python "$CLAUDE_CONFIG_DIR/skills/apply-discount/scripts/discount.py" SILVER 480
```

You report:
```
SILVER tier · −X% off · final $Y.YY
{"tier": "SILVER", "original_amount": 480.0, "discount_pct": ..., "discount_amount": ..., "final_amount": ...}
```

(The exact percentage and amounts come from the script's output — do
not fill them in from memory.)

## Rules

- ALWAYS run the script. Never guess the discount.
- Pass the tier name in uppercase; the script normalizes anyway.
- If the script prints `{"error": ...}` on stderr, surface that error
  to the user verbatim. Do NOT invent a fallback discount.
