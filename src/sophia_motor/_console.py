# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Interactive console for Motor — a chat-like REPL with live streaming.

Spawned by `await motor.console()`. Renders the agent's stream in real
time (text deltas, tool use lifecycle, file outputs, errors) using
`rich`; reads user input via `prompt-toolkit` so you get history,
multiline, and slash-command autocomplete out of the box.

Both deps are extras — if `Motor.console()` is called without them
installed, the call raises ImportError with the install hint.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .motor import Motor


_INSTALL_HINT = (
    "Motor.console() requires the [console] extras.\n"
    "Install with:  pip install sophia-motor[console]"
)


def _import_or_raise() -> dict[str, Any]:
    """Lazy-import rich + prompt-toolkit. Raise with install hint if missing."""
    try:
        from rich.console import Console
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import InMemoryHistory
    except ImportError as e:  # pragma: no cover — covered by docstring
        raise ImportError(f"{_INSTALL_HINT}\n\nOriginal error: {e}") from e
    return {
        "Console": Console,
        "Live": Live,
        "Panel": Panel,
        "Table": Table,
        "Text": Text,
        "PromptSession": PromptSession,
        "WordCompleter": WordCompleter,
        "InMemoryHistory": InMemoryHistory,
    }


_SLASH_COMMANDS = ["/help", "/exit", "/quit", "/q", "/files", "/audit", "/clear"]


HELP_TEXT = """\
[bold]Slash commands[/bold]

  [cyan]/help[/cyan]       Show this help
  [cyan]/exit[/cyan]       Quit the console (also: /q, /quit, Ctrl+D)
  [cyan]/files[/cyan]      List output files of the last run + persist hint
  [cyan]/audit[/cyan]      Print audit dump path of the last run
  [cyan]/clear[/cyan]      Clear screen

[bold]Keys[/bold]

  [cyan]Up/Down[/cyan]     Prompt history
  [cyan]Ctrl+C[/cyan]      Interrupt the running task (does NOT exit the console)
  [cyan]Ctrl+D[/cyan]      Exit
  [cyan]Esc Enter[/cyan]   Submit a multiline prompt
"""


async def run_console(motor: "Motor") -> None:  # noqa: PLR0915
    """Open a chat-like REPL bound to `motor`.

    Blocks until the user issues `/exit` (or hits Ctrl+D). Each input
    triggers a `motor.stream(RunTask(prompt=...))` whose chunks are
    rendered live; `motor`'s configured `default_*` (tools, system,
    attachments, skills, …) drive the runs, so the dev pre-configures
    a "ready-to-talk" motor and then just types prompts.
    """
    # Local import — defers the rich/prompt-toolkit dependency to call site.
    deps = _import_or_raise()
    Console = deps["Console"]
    Panel = deps["Panel"]
    Table = deps["Table"]
    Text = deps["Text"]
    PromptSession = deps["PromptSession"]
    WordCompleter = deps["WordCompleter"]
    InMemoryHistory = deps["InMemoryHistory"]
    from prompt_toolkit.formatted_text import HTML

    # Late SDK-side imports so the symbol set matches whatever the
    # user's installed motor version exposes.
    from ._chunks import (
        DoneChunk,
        ErrorChunk,
        InitChunk,
        OutputFileReadyChunk,
        RunStartedChunk,
        TextDeltaChunk,
        TextBlockChunk,
        ThinkingDeltaChunk,
        ToolResultChunk,
        ToolUseFinalizedChunk,
        ToolUseStartChunk,
    )
    from ._models import RunTask

    # Motor's default console logger and event printer would clutter
    # the TUI. Detach them for the session, restore on exit.
    from .events import default_console_event_logger, default_console_logger
    saved_event_subs = list(motor.events._event_subs)
    saved_log_subs = list(motor.events._log_subs)
    motor.events._event_subs = [
        s for s in motor.events._event_subs if s is not default_console_event_logger
    ]
    motor.events._log_subs = [
        s for s in motor.events._log_subs if s is not default_console_logger
    ]

    console = Console()
    session = PromptSession(
        history=InMemoryHistory(),
        completer=WordCompleter(_SLASH_COMMANDS, ignore_case=True),
        multiline=False,
    )

    # ── header ───────────────────────────────────────────────────────
    cfg = motor.config
    header = Table.grid(padding=(0, 2))
    header.add_column(style="dim", justify="right", min_width=14)
    header.add_column()
    header.add_row("model", f"[bold]{cfg.model}[/bold]")
    header.add_row("upstream", cfg.upstream_base_url)
    adapter_name = (
        cfg.upstream_adapter
        if isinstance(cfg.upstream_adapter, str)
        else type(cfg.upstream_adapter).__name__
    )
    header.add_row("adapter", str(adapter_name))
    if cfg.default_tools:
        header.add_row("tools", ", ".join(cfg.default_tools))
    if cfg.default_skills:
        header.add_row("skills", str(cfg.default_skills))
    if cfg.default_system:
        header.add_row("system", cfg.default_system[:80] + ("…" if len(cfg.default_system) > 80 else ""))
    console.print(Panel(header, title="sophia-motor", border_style="cyan"))
    console.print("[dim]/help for commands · /exit to quit · Ctrl+C interrupts a run[/dim]\n")

    last_run_result = None  # populated from each DoneChunk

    while True:
        try:
            prompt = await session.prompt_async(HTML("<ansicyan><b>></b></ansicyan> "))
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]bye[/dim]")
            break

        prompt = prompt.strip()
        if not prompt:
            continue

        # ── slash commands ───────────────────────────────────────────
        if prompt.startswith("/"):
            cmd = prompt.split()[0].lower()
            if cmd in ("/exit", "/quit", "/q"):
                console.print("[dim]bye[/dim]")
                break
            elif cmd == "/help":
                console.print(Panel(HELP_TEXT, title="help", border_style="dim"))
            elif cmd == "/files":
                if last_run_result is None or not last_run_result.output_files:
                    console.print("[dim]no output files in the last run[/dim]")
                else:
                    files_table = Table(show_header=True, header_style="bold")
                    files_table.add_column("relative_path")
                    files_table.add_column("size", justify="right")
                    files_table.add_column("mime")
                    for f in last_run_result.output_files:
                        files_table.add_row(f.relative_path, str(f.size), f.mime)
                    console.print(files_table)
                    console.print("[dim]copy with: result.output_files[i].copy_to(Path('./generated'))[/dim]")
            elif cmd == "/audit":
                if last_run_result is None:
                    console.print("[dim]no run yet[/dim]")
                else:
                    console.print(f"audit dump: [cyan]{last_run_result.audit_dir}[/cyan]")
            elif cmd == "/clear":
                console.clear()
            else:
                console.print(f"[red]unknown command:[/red] {cmd}  [dim](try /help)[/dim]")
            continue

        # ── run ──────────────────────────────────────────────────────
        in_text = False
        in_thinking = False
        try:
            async for chunk in motor.stream(RunTask(prompt=prompt)):
                if isinstance(chunk, RunStartedChunk):
                    console.print(f"[dim]run {chunk.run_id}[/dim]")
                elif isinstance(chunk, InitChunk):
                    pass  # session id is in the audit dump if anyone needs it
                elif isinstance(chunk, ThinkingDeltaChunk):
                    if not in_thinking:
                        console.print("[magenta]thinking ▸[/magenta] ", end="")
                        in_thinking = True
                    console.print(f"[magenta]{chunk.text}[/magenta]", end="")
                elif isinstance(chunk, ToolUseStartChunk):
                    if in_thinking:
                        console.print()  # newline after thinking
                        in_thinking = False
                    console.print(f"[yellow][{chunk.tool} …][/yellow]", end="")
                elif isinstance(chunk, ToolUseFinalizedChunk):
                    args = (
                        chunk.input.get("file_path")
                        or chunk.input.get("pattern")
                        or chunk.input.get("command")
                        or ""
                    )
                    args_short = args if len(args) < 80 else args[:77] + "…"
                    console.print(f"\r[yellow][{chunk.tool}][/yellow] {args_short}")
                elif isinstance(chunk, OutputFileReadyChunk):
                    console.print(f"  [green]✓ wrote[/green] {chunk.relative_path}")
                elif isinstance(chunk, ToolResultChunk):
                    snippet = chunk.preview.replace("\n", " ⏎ ")[:70]
                    color = "red" if chunk.is_error else "green"
                    mark = "✗" if chunk.is_error else "✓"
                    console.print(f"  [{color}]{mark} {snippet}[/{color}]")
                elif isinstance(chunk, TextDeltaChunk):
                    if not in_text:
                        if in_thinking:
                            console.print()
                            in_thinking = False
                        console.print("\n[cyan]answer ▸[/cyan] ", end="")
                        in_text = True
                    console.print(f"[cyan]{chunk.text}[/cyan]", end="")
                elif isinstance(chunk, TextBlockChunk):
                    # Fallback (no deltas streamed for this turn)
                    console.print(f"\n[cyan]answer ▸ {chunk.text}[/cyan]")
                elif isinstance(chunk, ErrorChunk):
                    console.print(f"\n[red]error: {chunk.message}[/red]")
                elif isinstance(chunk, DoneChunk):
                    last_run_result = chunk.result
                    m = chunk.result.metadata
                    if in_text or in_thinking:
                        console.print()
                    tag = (
                        "[yellow]INTERRUPTED[/yellow]" if m.was_interrupted
                        else ("[red]ERROR[/red]" if m.is_error else "[green]ok[/green]")
                    )
                    console.print(
                        f"[dim]── {tag} · turns={m.n_turns} tools={m.n_tool_calls} "
                        f"cost=${m.total_cost_usd:.4f} {m.duration_s:.1f}s ──[/dim]\n"
                    )
        except KeyboardInterrupt:
            # Active run: ask the motor to interrupt; the stream loop
            # will then yield a DoneChunk with was_interrupted=True.
            console.print("\n[yellow]interrupting…[/yellow]")
            try:
                await motor.interrupt()
            except RuntimeError:
                # Multiple runs ambiguity — shouldn't happen in console
                # mode (one run at a time), but handle it gracefully.
                pass

    # Restore the original subscribers so the motor logs normally
    # again if the caller continues using it after the console exits.
    motor.events._event_subs = saved_event_subs
    motor.events._log_subs = saved_log_subs
    await motor.stop()
