# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Event bus for the motor.

Publishes two streams:
- `Event` — structured events for telemetry/UI consumers (turn started, tool used, ...)
- `LogRecord` — leveled logs for human/console observability

Both streams use sync OR async callbacks (auto-detected). Subscribers are
registered as decorators OR by direct call:

    @motor.on_event
    async def handle(event): ...

    motor.on_log(my_logger)
"""
from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Literal


EventCallback = Callable[["Event"], Any | Coroutine[Any, Any, Any]]
LogCallback = Callable[["LogRecord"], Any | Coroutine[Any, Any, Any]]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


@dataclass
class Event:
    """Structured event emitted during a motor run."""
    type: str
    payload: dict
    run_id: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class LogRecord:
    """Human-readable log line."""
    level: LogLevel
    message: str
    run_id: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fields: dict = field(default_factory=dict)


class EventBus:
    """Pub/sub for Event and LogRecord streams.

    Subscribers may be sync or async. Errors raised inside a subscriber
    are swallowed (logged to stderr) so a buggy listener never breaks
    the motor.
    """

    def __init__(self) -> None:
        self._event_subs: list[EventCallback] = []
        self._log_subs: list[LogCallback] = []

    def on_event(self, fn: EventCallback) -> EventCallback:
        """Register a subscriber for `Event`. Usable as decorator or direct call."""
        self._event_subs.append(fn)
        return fn

    def on_log(self, fn: LogCallback) -> LogCallback:
        """Register a subscriber for `LogRecord`. Usable as decorator or direct call."""
        self._log_subs.append(fn)
        return fn

    async def emit_event(self, event: Event) -> None:
        for sub in list(self._event_subs):
            try:
                result = sub(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:  # noqa: BLE001
                print(f"[sophia-motor] event subscriber raised: {e!r}", file=sys.stderr)

    async def emit_log(self, record: LogRecord) -> None:
        for sub in list(self._log_subs):
            try:
                result = sub(record)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:  # noqa: BLE001
                print(f"[sophia-motor] log subscriber raised: {e!r}", file=sys.stderr)

    async def log(
        self,
        level: LogLevel,
        message: str,
        *,
        run_id: str | None = None,
        **fields: Any,
    ) -> None:
        """Convenience helper: build a LogRecord and emit it."""
        await self.emit_log(LogRecord(
            level=level,
            message=message,
            run_id=run_id,
            fields=fields,
        ))


# ─────────────────────────────────────────────────────────────────────────
# Default console subscribers (opt-in via MotorConfig.console_log_enabled)
# ─────────────────────────────────────────────────────────────────────────

_LEVEL_COLOR = {
    "DEBUG":   "\033[90m",  # grey
    "INFO":    "\033[36m",  # cyan
    "WARNING": "\033[33m",  # yellow
    "ERROR":   "\033[31m",  # red
}
_RESET = "\033[0m"
_EVENT_COLOR = "\033[35m"  # magenta


async def default_console_logger(record: LogRecord) -> None:
    """Print a formatted log line to stdout."""
    color = _LEVEL_COLOR.get(record.level, "")
    ts = record.ts.strftime("%H:%M:%S")
    fields = ""
    if record.fields:
        fields = " " + " ".join(f"{k}={v}" for k, v in record.fields.items() if v is not None)
    print(f"{color}[{ts}] {record.level:<7} {record.message}{fields}{_RESET}")


async def default_console_event_logger(event: Event) -> None:
    """Print a one-line summary of each event to stdout."""
    ts = event.ts.strftime("%H:%M:%S")
    payload_str = str(event.payload)
    if len(payload_str) > 220:
        payload_str = payload_str[:220] + "..."
    print(f"{_EVENT_COLOR}[{ts}] EVENT   {event.type:<18} {payload_str}{_RESET}")
