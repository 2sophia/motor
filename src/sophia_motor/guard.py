# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Built-in PreToolUse hook — sandbox the agent inside its workspace.

Three modes (selected by `MotorConfig.guardrail`):

  - "strict"      (default): restrictive — Read/Edit/Glob/Grep must stay
                  inside cwd; Write must target outputs/; Bash blocks
                  developer/admin commands (curl, wget, ssh, git, docker,
                  pip, node, sudo, chmod, ...) plus `..`, `/dev/tcp`,
                  `bash -c`, `eval`/`exec`/`source` patterns.

  - "permissive": minimum sane blocks only — `..` path escapes, `/dev/tcp`,
                  `sudo`, `rm -rf /` and obvious exfiltration (`curl`,
                  `wget` with `-d`/`--data`/redirects). Useful for trusted
                  dev workflows where the agent legitimately needs git,
                  package managers, etc.

  - "off":        no hook at all — full SDK behaviour. Use only when you
                  fully control the prompts AND the host the agent runs on
                  (e.g. ephemeral container, dedicated VM).

The hook returns `{}` to allow, or `{"decision": "block", "reason": "..."}`
to refuse the tool call. The agent receives the reason as feedback and
typically retries with a corrected approach.
"""
from __future__ import annotations

import logging
import os.path
import pathlib
import re
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger("sophia_motor.guard")

GuardrailMode = Literal["strict", "permissive", "off"]
HookCallback = Callable[[dict, Any, Any], Awaitable[dict]]


# ─────────────────────────────────────────────────────────────────────────
# Bash blocklist patterns
# ─────────────────────────────────────────────────────────────────────────

# Commands to flat-out reject when they appear at command-word position.
# Aimed at developer/admin tooling that has no place inside an agent run.
_STRICT_BLOCKED_CMDS = frozenset({
    # remote / network
    "ssh", "scp", "sftp", "rsync", "telnet", "nc", "ncat", "netcat",
    "curl", "wget", "ftp", "tftp",
    # version control / build / deploy
    "git", "hg", "svn",
    "docker", "podman", "buildah", "kubectl", "helm",
    "make", "cmake", "ninja",
    # package managers / installers
    "apt", "apt-get", "yum", "dnf", "pacman", "snap", "flatpak", "brew",
    "pip", "pip3", "pipx", "poetry", "uv", "conda", "mamba",
    "npm", "yarn", "pnpm", "node", "deno", "bun",
    "gem", "cargo", "go", "dotnet",
    # privilege escalation / system control
    "sudo", "doas", "su", "pkexec",
    "chmod", "chown", "chgrp", "setcap", "setfacl",
    "systemctl", "service", "rc-service",
    # process control / kill
    "kill", "pkill", "killall", "reboot", "shutdown", "halt", "poweroff",
    # mount / partition
    "mount", "umount", "fdisk", "mkfs", "parted",
    # symlink / hardlink creation — would let the agent plant a symlink
    # to escape cwd at the next Read; lexical path check below trusts
    # that no new symlinks appear during a run.
    "ln", "link", "symlink",
    # crontab / timers
    "crontab", "at", "batch",
    # firewall / network config
    "iptables", "nft", "ufw", "firewall-cmd",
    "ip", "route", "ifconfig", "iwconfig",
    # interactive shells / editors that escape
    "vi", "vim", "emacs", "nano", "less", "more",
    # misc dangerous
    "dd", "shred", "wipe",
})

# Permissive mode keeps a much smaller set — only obvious exfiltration
# and privilege escalation. Git/docker/pip/etc. are allowed here.
_PERMISSIVE_BLOCKED_CMDS = frozenset({
    "sudo", "doas", "su", "pkexec",
    "ssh", "scp", "sftp", "telnet", "nc", "ncat", "netcat",
    "reboot", "shutdown", "halt", "poweroff",
    "dd", "shred", "wipe",
    "mkfs", "fdisk", "parted",
})

# Patterns blocked in BOTH strict and permissive — sandbox-escape vectors.
_SPECIAL_BLOCKED_RE: list[re.Pattern[str]] = [
    re.compile(r"/dev/tcp\b", re.IGNORECASE),
    re.compile(r"/dev/udp\b", re.IGNORECASE),
    # stdin/fd redirects — bypass for `python < script.py` or
    # `python -c "$(cat /dev/stdin)"`-style tricks.
    re.compile(r"/dev/stdin\b", re.IGNORECASE),
    re.compile(r"/dev/fd/\d+", re.IGNORECASE),
    # piping into a python interpreter is the cat-pipe-python evasion:
    # `cat foo.py | python` reads code from stdin, dodging both the
    # python-c parser and the skill-path whitelist below.
    re.compile(r"\|\s*python3?\b"),
    re.compile(r"\bbash\s+-c\b"),
    re.compile(r"\bsh\s+-c\b"),
    re.compile(r"\bzsh\s+-c\b"),
    re.compile(r"\bexec\s+\d*[<>]"),  # exec 3<>/dev/tcp/...
    re.compile(r"\beval\s+"),
    re.compile(r"\bsource\s+\S"),
    re.compile(r"\.\s+/"),  # `. /path/to/script` (sourcing absolute)
    # python / node escape hatches (-m specials still match here for
    # permissive mode; strict has its own python-invocation guard)
    re.compile(r"\bpython3?\s+-m\s+(pip|venv|http\.server|smtpd)\b"),
    re.compile(r"\bnode\s+-e\b"),
    re.compile(r"\bperl\s+-e\b"),
    re.compile(r"\bruby\s+-e\b"),
    # rm -rf at filesystem root or HOME
    re.compile(r"\brm\s+-[rRf]+\s+(/|~|\$HOME)"),
    # curl/wget with data exfiltration shapes (only checked in strict —
    # permissive lets these through; we still block them in strict via
    # _STRICT_BLOCKED_CMDS above)
]


# ─────────────────────────────────────────────────────────────────────────
# Strict-mode Python invocation guard
# ─────────────────────────────────────────────────────────────────────────
#
# `python` is allowed in strict mode (skills like python-math need it),
# but the call shape is constrained:
#
#   python -c "<code>"              → code is parsed; only stdlib-safe
#                                     imports allowed, no exec/eval/__import__,
#                                     no os/subprocess/socket/shutil access
#   python <skill-script-path>      → only paths under
#                                     $CLAUDE_CONFIG_DIR/skills/<name>/scripts/
#                                     (a skill registered by the dev = trust
#                                     passport). Anything else (outputs/,
#                                     attachments/, /tmp, ...) is blocked.
#   python <anything-else>          → block (REPL, -i, -m non-whitelisted, ...)
#
# All checks are best-effort lexical. Determined evasion via heavy
# obfuscation is still possible — the goal is to defeat the common
# prompt-injection / accidental-mistake cases without breaking honest
# skills.

# Top-level modules that can be imported inside `python -c "..."` without
# tripping the guard. Stdlib-only, computation-shaped, no I/O surface.
_PYTHON_C_ALLOWED_MODULES = frozenset({
    "math", "statistics", "decimal", "fractions",
    "json", "re", "datetime", "random",
    "itertools", "functools", "collections",
    "string", "textwrap", "unicodedata",
    "base64", "hashlib", "uuid", "time",
    "operator", "copy", "enum", "typing",
})

# Token patterns rejected outright inside `python -c "..."`.
_PYTHON_C_BLOCKED_TOKENS: list[re.Pattern[str]] = [
    re.compile(r"__import__\s*\("),
    re.compile(r"__builtins__"),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bgetattr\s*\("),
    re.compile(r"\bsetattr\s*\("),
    re.compile(r"\bopen\s*\(\s*[\"']/"),  # open with absolute path
    re.compile(r"\bopen\s*\(\s*0\b"),     # open(0) reads stdin
    re.compile(r"\bos\."),                 # os.system / os.popen / os.environ / ...
    re.compile(r"\bsubprocess\."),
    re.compile(r"\bsocket\."),
    re.compile(r"\bshutil\."),
    re.compile(r"\bsys\.exit"),
    re.compile(r"\bctypes\b"),
]

# Match `import X[, Y]` and `from X import ...` — extracts the top-level
# module name(s) for whitelist enforcement.
_PYTHON_IMPORT_RE = re.compile(
    r"\bfrom\s+([\w.]+)\s+import\b|\bimport\s+([\w.,\s]+)"
)


def _check_python_c(code: str) -> str | None:
    """Return a block-reason if `code` violates the python-c policy, else None."""
    # Bash-escape unwrap: `python -c "open(\"/etc/passwd\")"` arrives at the
    # guard with the backslashes still in place — at exec time bash strips
    # them and the interpreter sees `open("/etc/passwd")`. Mirror that
    # transformation so the patterns below match what python will see.
    code = code.replace('\\"', '"').replace("\\'", "'")
    for pat in _PYTHON_C_BLOCKED_TOKENS:
        m = pat.search(code)
        if m:
            return (
                f"python -c: pattern '{m.group(0).strip()}' is not allowed — "
                f"sandbox-escape surface."
            )
    for match in _PYTHON_IMPORT_RE.finditer(code):
        # Either group(1) (from X import ...) or group(2) (import X[, Y])
        raw = match.group(1) or match.group(2) or ""
        for piece in raw.split(","):
            mod = piece.strip().split(" as ")[0].strip().split(".")[0]
            if mod and mod not in _PYTHON_C_ALLOWED_MODULES:
                return (
                    f"python -c: import '{mod}' is not in the stdlib-safe "
                    f"whitelist (math, statistics, json, decimal, ...)."
                )
    return None


def _is_skill_script_path(path: str, cwd: str) -> bool:
    """True iff `path` resolves under `<cwd>/../.claude/skills/<name>/scripts/`.

    Handles both the `$CLAUDE_CONFIG_DIR` / `${CLAUDE_CONFIG_DIR}` shell
    expansion (the agent's idiomatic way) and an absolute path that the
    agent might have learned via `env`. Lexical normalization only — no
    symlink follow, no FS access. The motor places the skills tree under
    `<run>/.claude/skills/` and the agent cwd is `<run>/agent_cwd/`, so
    the parent of cwd is the run root.
    """
    if not path or not cwd:
        return False
    claude_dir = str(pathlib.Path(cwd).parent / ".claude")
    expanded = path.replace("${CLAUDE_CONFIG_DIR}", claude_dir)
    expanded = expanded.replace("$CLAUDE_CONFIG_DIR", claude_dir)
    if not expanded.startswith("/"):
        return False
    normalized = os.path.normpath(expanded)
    skills_root = claude_dir.rstrip("/") + "/skills/"
    if not normalized.startswith(skills_root):
        return False
    rel = normalized[len(skills_root):]
    parts = rel.split("/")
    # Must be <name>/scripts/<file...>
    return len(parts) >= 3 and parts[1] == "scripts" and parts[2] != ""


# Match `python` / `python3` invocations. The args group repeats over
# whitespace-separated tokens that are either (a) double-quoted strings,
# (b) single-quoted strings, or (c) bare tokens with no shell separator
# / quote. This is the only way to capture `-c "import math; print(...)"`
# whole — a naïve lazy `.*?` would truncate at the `;` inside the code
# or at the `)` inside `print(x)`.
_PYTHON_INVOKE_RE = re.compile(
    r"""
    (?:^|[\s;|&])         # boundary
    (python3?)            # interpreter
    (                     # args (possibly empty for bare `python`):
        (?:
            \s+
            (?:
                "(?:\\.|[^"])*"   # double-quoted token (handles \")
                | '(?:\\.|[^'])*' # single-quoted token
                | [^"'\s;|&]+     # bare token
            )
        )*
    )
    """,
    re.VERBOSE,
)
# Inside the captured args, recognize `-c '<code>'` / `-c "<code>"`.
_PYTHON_DASH_C_RE = re.compile(
    r"^-c\s+(['\"])(.*)\1\s*(?:\S.*)?$",
    re.DOTALL,
)


def _check_python_invocation(command: str, cwd: str) -> str | None:
    """Walk every `python[3]` call in `command`. Return the first block reason
    (or None if every invocation passes). Strict mode only.
    """
    for m in _PYTHON_INVOKE_RE.finditer(command):
        args = m.group(2).strip()
        if not args:
            return (
                "python invoked with no arguments → opens an interactive "
                "REPL or reads stdin. Use `python -c \"...\"` with a "
                "stdlib-safe expression, or call a skill script."
            )
        if args.startswith("-c"):
            cm = _PYTHON_DASH_C_RE.match(args)
            if not cm:
                return (
                    "python -c: the code argument must be a single quoted "
                    "string (single or double quotes)."
                )
            reason = _check_python_c(cm.group(2))
            if reason:
                return reason
            continue
        if args.startswith("-"):
            # -m, -i, -h, -V, -O, -u, -W, -X — block the lot in strict.
            flag = args.split()[0]
            return (
                f"python {flag}: only `python -c \"<code>\"` or "
                f"`python <skill-script-path>` are allowed in strict mode."
            )
        # Positional path: must be a skill script. Strip the bash quotes
        # the agent might have wrapped around `$CLAUDE_CONFIG_DIR/...`.
        path = args.split()[0]
        if len(path) >= 2 and path[0] == path[-1] and path[0] in ('"', "'"):
            path = path[1:-1]
        if not _is_skill_script_path(path, cwd):
            return (
                f"python {path}: only skill scripts are runnable "
                f"(paths under $CLAUDE_CONFIG_DIR/skills/<name>/scripts/). "
                f"Files in outputs/, attachments/, or arbitrary locations "
                f"are not — register a skill if you need this script."
            )
    return None

# Exfiltration patterns that we want blocked even in permissive mode
# (bypass: still match before the per-mode command list).
_EXFIL_RE = re.compile(
    r"\b(curl|wget)\s+[^|;&]*?\s+(-d|--data|--data-binary|-T|--upload-file)\b",
    re.IGNORECASE,
)

# `..` path component as escape vector.
_DOT_DOT_RE = re.compile(r"(?:^|[\s=(])\.\.(?:/|\s|$|\))|/\.\.(?:/|\s|$)")

# Tokenize command-word positions.
_CMD_WORD_RE = re.compile(r"(?:^|[;|&]|&&|\|\|)\s*([\w./-]+)")


# ─────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────

def _resolve_under_cwd(path: str, cwd: str) -> str | None:
    """Resolve `path` against cwd, following symlinks. Returns None on error.

    Used by Write to detect symlink-escape into outputs/ (where we DO
    want anti-escape semantics, since the agent could otherwise plant a
    symlink earlier and then write through it).
    """
    if not cwd:
        return None
    try:
        target = pathlib.Path(path) if path.startswith("/") else pathlib.Path(cwd) / path
        return str(target.resolve(strict=False))
    except (OSError, RuntimeError):
        return None


def _is_path_in_cwd(path: str, cwd_resolved: str, cwd: str) -> bool:
    """True if `path` is lexically inside `cwd` (no symlink following).

    Lexical-only check by design: the motor places intentional symlinks
    under `attachments/` (and the CLI places skill symlinks under
    `.claude/`) that point at locations outside cwd. Following those at
    Read/Glob/Grep time would be a self-inflicted denial of service —
    every Path-based attachment would be blocked from being read.

    Symlink-escape resistance is enforced upstream: agent-driven symlink
    creation via Bash (`ln`, `link`, `cp -s`, `tar -h`, etc.) is in the
    strict blocklist. Write is checked separately with full resolution
    (see `_resolve_under_cwd` use in the Write branch).

    Implementation: build the absolute path WITHOUT following links,
    normalize `..` and `.` lexically with `os.path.normpath`, then
    require the result to be exactly `cwd_resolved` or a child of it.
    """
    if not path:
        return True
    if not cwd:
        return not path.startswith("/")
    abs_path = path if path.startswith("/") else os.path.join(cwd, path)
    normalized = os.path.normpath(abs_path)
    cwd_prefix = cwd_resolved.rstrip("/")
    return normalized == cwd_prefix or normalized.startswith(cwd_prefix + "/")


# ─────────────────────────────────────────────────────────────────────────
# Hook factory
# ─────────────────────────────────────────────────────────────────────────

def make_guard_hook(mode: GuardrailMode) -> HookCallback | None:
    """Return a PreToolUse async hook for the given mode, or None for "off".

    The hook signature matches `claude_agent_sdk.HookCallback`:
        async def hook(hook_input: dict, _result: Any, _context: Any) -> dict
    """
    if mode == "off":
        return None

    blocked_cmds = (
        _STRICT_BLOCKED_CMDS if mode == "strict" else _PERMISSIVE_BLOCKED_CMDS
    )

    async def _hook(hook_input: dict, _result: Any, _context: Any) -> dict:
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {}) or {}
        cwd = hook_input.get("cwd", "") or ""

        cwd_resolved = ""
        if cwd:
            try:
                cwd_resolved = str(pathlib.Path(cwd).resolve(strict=False))
            except (OSError, RuntimeError):
                cwd_resolved = cwd

        # ── Read / Edit: path must be inside cwd ─────────────────────────
        if tool_name in ("Read", "Edit"):
            file_path = str(tool_input.get("file_path", ""))
            if mode == "strict" and not _is_path_in_cwd(file_path, cwd_resolved, cwd):
                logger.warning("[guard] BLOCKED %s outside cwd: %s", tool_name, file_path)
                return {
                    "decision": "block",
                    "reason": (
                        f"Path '{file_path}' is outside the workspace "
                        f"({cwd}). Use a path relative to your cwd, "
                        f"e.g. attachments/<file> or outputs/<file>."
                    ),
                }

        # ── Glob / Grep: path parameter inside cwd ───────────────────────
        if tool_name in ("Glob", "Grep"):
            path = str(tool_input.get("path", ""))
            if mode == "strict" and path and not _is_path_in_cwd(path, cwd_resolved, cwd):
                logger.warning("[guard] BLOCKED %s outside cwd: %s", tool_name, path)
                return {
                    "decision": "block",
                    "reason": (
                        f"Search path '{path}' is outside the workspace "
                        f"({cwd}). Search relative to cwd, e.g. "
                        f"path='.' or path='attachments/'."
                    ),
                }

        # ── Write: target must be in outputs/ (strict) ───────────────────
        if tool_name == "Write" and mode == "strict":
            file_path = str(tool_input.get("file_path", ""))
            ok = (
                file_path.startswith("outputs/")
                or file_path.startswith("./outputs/")
                or (cwd and file_path.startswith(f"{cwd}/outputs/"))
            )
            if not ok:
                basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
                logger.warning("[guard] BLOCKED Write outside outputs/: %s", file_path)
                return {
                    "decision": "block",
                    "reason": (
                        f"Write only to outputs/. "
                        f"Try Write(file_path='outputs/{basename}', ...)."
                    ),
                }
            if cwd:
                resolved = _resolve_under_cwd(file_path, cwd)
                outputs_root = str((pathlib.Path(cwd) / "outputs").resolve(strict=False))
                under = resolved and (
                    resolved == outputs_root or resolved.startswith(outputs_root + "/")
                )
                if not under:
                    logger.warning("[guard] BLOCKED Write symlink escape: %s → %s",
                                   file_path, resolved)
                    return {
                        "decision": "block",
                        "reason": (
                            "Write target resolves outside outputs/ "
                            "(possible symlink escape). Use a plain "
                            "filename inside outputs/."
                        ),
                    }

        # ── Bash: blocklist + special patterns ───────────────────────────
        if tool_name == "Bash":
            command = str(tool_input.get("command", ""))

            # 1. exfiltration via curl/wget with data flags (both modes)
            if _EXFIL_RE.search(command):
                logger.warning("[guard] BLOCKED Bash exfil pattern: %s", command[:200])
                return {
                    "decision": "block",
                    "reason": (
                        "Outbound data transfer (curl/wget with -d/--data/"
                        "--upload-file) is not allowed."
                    ),
                }

            # 2. special escape patterns (both modes)
            for pat in _SPECIAL_BLOCKED_RE:
                m = pat.search(command)
                if m:
                    matched = m.group(0)
                    logger.warning("[guard] BLOCKED Bash pattern '%s': %s",
                                   matched, command[:200])
                    return {
                        "decision": "block",
                        "reason": (
                            f"Pattern '{matched.strip()}' is not allowed — "
                            f"it can be used to escape the sandbox."
                        ),
                    }

            # 3. `..` path escapes
            if _DOT_DOT_RE.search(command):
                logger.warning("[guard] BLOCKED Bash '..' escape: %s", command[:200])
                return {
                    "decision": "block",
                    "reason": (
                        "Don't use `..` in paths — it tries to navigate "
                        "outside the workspace."
                    ),
                }

            # 4. python invocation guard (strict only) — covers the
            #    Write+exec workaround. python script is allowed only
            #    when it's a registered skill script; -c is allowed only
            #    with stdlib-safe imports + no os/subprocess/exec/eval.
            if mode == "strict":
                py_reason = _check_python_invocation(command, cwd_resolved or cwd)
                if py_reason:
                    logger.warning("[guard] BLOCKED Bash python: %s", command[:200])
                    return {"decision": "block", "reason": py_reason}

            # 5. command-word blocklist (strict vs permissive)
            for m in _CMD_WORD_RE.finditer(command):
                token = m.group(1)
                basename = token.rsplit("/", 1)[-1].lower()
                if basename in blocked_cmds:
                    logger.warning("[guard] BLOCKED Bash command '%s': %s",
                                   basename, command[:200])
                    if mode == "strict":
                        reason = (
                            f"Command '{basename}' is blocked in strict mode. "
                            f"This agent runs in a sandboxed workspace — no "
                            f"developer/admin tooling. Use Python "
                            f"(pandas, pypdf, ...) or built-in tools instead."
                        )
                    else:
                        reason = (
                            f"Command '{basename}' is blocked: privilege "
                            f"escalation or destructive system call."
                        )
                    return {"decision": "block", "reason": reason}

        return {}

    return _hook
