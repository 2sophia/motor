# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Unit tests for proxy body transformations.

These run without ANTHROPIC_API_KEY (no SDK call). They exercise the pure
helper functions that munge the request body before forwarding upstream.
"""
from __future__ import annotations

from pathlib import Path


from sophia_motor.proxy import (
    _rewrite_tool_descriptions,
    _strip_sdk_noise,
    _strip_user_system_reminders,
)


# ─────────────────────────────────────────────────────────────────────────
# _strip_user_system_reminders
# ─────────────────────────────────────────────────────────────────────────

def test_strip_no_reminders_noop():
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 0
    assert body["messages"][0]["content"][0]["text"] == "hello"


def test_strip_block_entirely_reminder_drops_block():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "real prompt"},
                {"type": "text", "text": "<system-reminder>\nskill listing\n</system-reminder>"},
            ]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 1
    assert len(body["messages"][0]["content"]) == 1
    assert body["messages"][0]["content"][0]["text"] == "real prompt"


def test_strip_mixed_keeps_residual():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": (
                    "<system-reminder>note A</system-reminder>\n\n"
                    "Continue the analysis."
                )},
            ]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 1
    assert body["messages"][0]["content"][0]["text"] == "Continue the analysis."


def test_strip_multiple_reminders_in_one_block():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": (
                    "<system-reminder>A</system-reminder>"
                    "<system-reminder>B</system-reminder>"
                    "real text"
                )},
            ]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 2
    assert body["messages"][0]["content"][0]["text"] == "real text"


def test_strip_only_user_messages_not_assistant():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>x</system-reminder>"},
            ]},
            {"role": "assistant", "content": [
                {"type": "text", "text": "<system-reminder>not stripped</system-reminder>"},
            ]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 1
    # user got block dropped (it was entirely reminder)
    assert body["messages"][0]["content"] == []
    # assistant untouched
    assert "<system-reminder>" in body["messages"][1]["content"][0]["text"]


def test_strip_string_content_not_list_is_safe():
    """Some messages have content as plain string — must not crash."""
    body = {
        "messages": [
            {"role": "user", "content": "plain string"},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 0


def test_strip_non_text_block_passes_through():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "content": "<system-reminder>x</system-reminder>"},
                {"type": "text", "text": "<system-reminder>strip me</system-reminder>"},
            ]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 1
    # tool_result kept (we only touch type=text user blocks)
    assert body["messages"][0]["content"][0]["type"] == "tool_result"


def test_strip_dotall_multiline_reminder():
    body = {
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": (
                    "<system-reminder>\nline1\nline2\nline3\n</system-reminder>\n"
                    "user text"
                )},
            ]},
        ],
    }
    n = _strip_user_system_reminders(body)
    assert n == 1
    assert body["messages"][0]["content"][0]["text"] == "user text"


# ─────────────────────────────────────────────────────────────────────────
# _strip_sdk_noise
# ─────────────────────────────────────────────────────────────────────────

def test_strip_sdk_noise_removes_billing_and_identity():
    body = {
        "system": [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=1; …"},
            {"type": "text", "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK."},
            {"type": "text", "text": "Real system prompt content."},
        ],
    }
    n = _strip_sdk_noise(body)
    assert n == 2
    assert len(body["system"]) == 1
    assert body["system"][0]["text"] == "Real system prompt content."


def test_strip_sdk_noise_no_match_noop():
    body = {"system": [{"type": "text", "text": "ok"}]}
    n = _strip_sdk_noise(body)
    assert n == 0


# ─────────────────────────────────────────────────────────────────────────
# _rewrite_tool_descriptions
# ─────────────────────────────────────────────────────────────────────────

def test_rewrite_tool_descriptions_replaces_match():
    body = {
        "tools": [
            {"name": "Read", "description": "old desc"},
            {"name": "Bash", "description": "old bash"},
        ],
    }
    n = _rewrite_tool_descriptions(body, {"Read": "new read"})
    assert n == 1
    assert body["tools"][0]["description"] == "new read"
    assert body["tools"][1]["description"] == "old bash"


def test_rewrite_tool_descriptions_empty_overrides_noop():
    body = {"tools": [{"name": "Read", "description": "old"}]}
    n = _rewrite_tool_descriptions(body, {})
    assert n == 0
