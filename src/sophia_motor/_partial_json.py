# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Best-effort parse of streaming `tool_use` input fragments.

The Anthropic streaming API emits `input_json_delta` chunks that build up
the JSON envelope of a tool_use input piece by piece. During the stream a
fragment may not be valid JSON yet:

    {"file_path": "outputs/repor
    {"file_path": "outputs/report.md", "content": "# Tito

This module returns a best-effort `dict` from such fragments so a UI can
render a live preview before the tool call commits.

Strategy:
  1. direct json.loads (works once the chunk is well-formed)
  2. heuristic close: balance quotes/braces and retry
  3. tolerant regex per known field of the given tool

Returns a dict (possibly empty). Never raises on malformed input.

Provenance: lifted from `sophia-agent/app/services/agent_service.py`.
"""
from __future__ import annotations

import json
import re


_TOOL_FIELDS_BY_NAME: dict[str, list[str]] = {
    "Write": ["file_path", "content"],
    "Edit": ["file_path", "old_string", "new_string"],
    "Bash": ["command", "description"],
    "Read": ["file_path"],
    "Glob": ["pattern", "path"],
    "Grep": ["pattern", "path"],
}


def parse_partial_tool_input(partial_json: str, tool_name: str) -> dict:
    """See module docstring."""
    if not partial_json:
        return {}
    try:
        return json.loads(partial_json)
    except json.JSONDecodeError:
        pass

    # Heuristic close: balance unescaped quotes + open braces, retry.
    try:
        fixed = partial_json
        unescaped_quotes = len(re.findall(r'(?<!\\)"', fixed))
        if unescaped_quotes % 2 == 1:
            fixed += '"'
        opens = fixed.count("{") - fixed.count("}")
        if opens > 0:
            fixed += "}" * opens
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Tolerant per-field regex extraction.
    fields = _TOOL_FIELDS_BY_NAME.get(
        tool_name, ["file_path", "content", "command"]
    )
    result: dict = {}
    for field in fields:
        m = re.search(
            rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)',
            partial_json,
            re.DOTALL,
        )
        if not m:
            continue
        raw = m.group(1)
        try:
            result[field] = json.loads(f'"{raw}"')
        except json.JSONDecodeError:
            result[field] = (
                raw.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
    return result
