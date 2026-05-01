# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""sophia-motor — instanceable agent motor wrapping Claude Agent SDK.

Public API:
    from sophia_motor import Motor, MotorConfig, RunTask, RunResult, clean_runs
"""
from ._chunks import (
    DoneChunk,
    ErrorChunk,
    InitChunk,
    OutputFileReadyChunk,
    RunStartedChunk,
    StreamChunk,
    TextBlockChunk,
    TextDeltaChunk,
    ThinkingBlockChunk,
    ThinkingDeltaChunk,
    ToolResultChunk,
    ToolUseCompleteChunk,
    ToolUseDeltaChunk,
    ToolUseFinalizedChunk,
    ToolUseStartChunk,
)
from ._models import OutputFile, RunMetadata, RunResult, RunTask
from .cleanup import clean_runs
from .config import MotorConfig
from .events import (
    Event,
    EventBus,
    LogRecord,
    default_console_event_logger,
    default_console_logger,
)
from .motor import Motor

__version__ = "0.0.1"

__all__ = [
    "Motor",
    "MotorConfig",
    "RunTask",
    "RunResult",
    "RunMetadata",
    "OutputFile",
    "Event",
    "LogRecord",
    "EventBus",
    "default_console_logger",
    "default_console_event_logger",
    "clean_runs",
    # streaming chunks
    "StreamChunk",
    "RunStartedChunk",
    "InitChunk",
    "TextDeltaChunk",
    "TextBlockChunk",
    "ThinkingDeltaChunk",
    "ThinkingBlockChunk",
    "ToolUseStartChunk",
    "ToolUseDeltaChunk",
    "ToolUseCompleteChunk",
    "ToolUseFinalizedChunk",
    "ToolResultChunk",
    "OutputFileReadyChunk",
    "ErrorChunk",
    "DoneChunk",
]
