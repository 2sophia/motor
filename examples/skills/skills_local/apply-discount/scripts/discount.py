#!/usr/bin/env python3
# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Tier-based discount calculator — bundled with the apply-discount skill.

The discount table is hardcoded here on purpose: it represents
proprietary business logic the agent cannot derive from public
knowledge. Calling this script is the ONLY way to get the correct
final price.

Usage:
    python discount.py <TIER> <AMOUNT>

Output (stdout): JSON with the calculated breakdown.
Errors           (stderr): JSON with an `error` field, exit code 2.
"""
from __future__ import annotations

import json
import sys


# Proprietary discount table. Real-world equivalents would live in a
# secured config or a database, but for the skill demo we hardcode it.
DISCOUNT_TABLE: dict[str, float] = {
    "PLATINUM": 0.20,
    "GOLD": 0.12,
    "SILVER": 0.06,
    "BRONZE": 0.02,
    "STANDARD": 0.0,
}


def main() -> int:
    if len(sys.argv) != 3:
        print(
            json.dumps({"error": "usage: discount.py <TIER> <AMOUNT>"}),
            file=sys.stderr,
        )
        return 2

    tier_raw, amount_raw = sys.argv[1], sys.argv[2]
    tier = tier_raw.upper()

    try:
        amount = float(amount_raw)
    except ValueError:
        print(
            json.dumps({"error": f"amount {amount_raw!r} is not numeric"}),
            file=sys.stderr,
        )
        return 2

    if tier not in DISCOUNT_TABLE:
        print(
            json.dumps({
                "error": f"unknown tier {tier_raw!r}",
                "valid_tiers": sorted(DISCOUNT_TABLE),
            }),
            file=sys.stderr,
        )
        return 2

    pct = DISCOUNT_TABLE[tier]
    discount_amount = round(amount * pct, 2)
    final_amount = round(amount - discount_amount, 2)

    print(json.dumps({
        "tier": tier,
        "original_amount": round(amount, 2),
        "discount_pct": pct,
        "discount_amount": discount_amount,
        "final_amount": final_amount,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
