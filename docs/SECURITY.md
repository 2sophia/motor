# Security model

The motor's security stance, written honestly. The strict guard is a
**lexical first filter** — it catches the common LLM mistake and the
naïve prompt injection. It is **not** a formal sandbox. For real
production use, layer OS-level isolation underneath (see
[Production hardening](#production-hardening) below).

---

## Three modes

| Mode           | Read / Edit / Glob / Grep | Write           | Bash                                                                                                                     |
|----------------|---------------------------|-----------------|--------------------------------------------------------------------------------------------------------------------------|
| **strict** (default) | must stay inside cwd | only `outputs/` | dev/admin commands blocked (`curl`, `git`, `docker`, `pip`, `npm`, `sudo`, ...) + `..` / `/dev/tcp` / `bash -c` / `eval` + Python invocation parser |
| **permissive** | unrestricted              | unrestricted    | only `sudo`, exfiltration patterns, `/dev/tcp`, `..` escapes, destructive commands                                       |
| **off**        | unrestricted              | unrestricted    | unrestricted                                                                                                             |

```python
Motor(MotorConfig(guardrail="strict"))      # default — safe by default
Motor(MotorConfig(guardrail="permissive"))  # blocks only sudo/exfil/escapes
Motor(MotorConfig(guardrail="off"))         # no hook (you take responsibility)
```

---

## What the motor controls (that the raw SDK doesn't)

The Claude Agent SDK ships a CLI that, by default, inherits the entire
environment of your Python process and runs Bash freely. If you embed
the raw SDK in a backend that has `MONGODB_URI`, `STRIPE_SECRET_KEY`,
or `AWS_ACCESS_KEY_ID` in its env, **the model can read them** with a
single `os.environ` print or `env` shell command. The motor closes the
common gaps:

| Layer | Raw SDK | sophia-motor |
|---|---|---|
| Subprocess env | full inherit (host secrets visible) | **only** `PATH`, `ANTHROPIC_API_KEY`, `CLAUDE_CONFIG_DIR`, model + `DISABLE_*` flags. Nothing else leaks |
| Filesystem reads | unrestricted | `Read/Edit/Glob/Grep` fenced inside the run cwd (strict) |
| Filesystem writes | unrestricted | `Write` restricted to `outputs/` (strict), with symlink-escape resolution |
| Bash blocklist | none | dev/admin commands + `bash -c` + `..` + `/dev/tcp` + `eval`/`source`/`exec` redirects |
| Exfiltration patterns | none | `curl`/`wget` with `--data`/`--upload-file` blocked in **both** strict and permissive |
| Per-run isolation | shared cwd | each run gets its own workspace under `<workspace_root>/<run_id>/`, deleted by `motor.clean_runs()` |
| Audit trail | none | every request/response body persisted under `<run>/audit/` (when `proxy_dump_payloads=True`) |

---

## Python invocation guard (strict mode only)

`python` and `python3` are allowed in strict mode but the call shape
is constrained:

| Form | Verdict |
|---|---|
| `python -c "<code>"` with stdlib-safe imports + no `os`/`subprocess`/`socket`/`shutil`/`exec`/`eval`/`__import__`/`open('/abs/path')` | ✅ allowed |
| `python <path>` where `<path>` is under `$CLAUDE_CONFIG_DIR/skills/<name>/scripts/` (a skill the dev registered) | ✅ allowed |
| `python -c "..."` with `import os`, `subprocess`, `shutil`, `socket`, `urllib`, `requests`, `__import__(...)`, `exec(...)`, `eval(...)`, `open('/abs/path')`, `open(0)`, `__builtins__`, `getattr(...)` | ❌ blocked |
| `python outputs/foo.py`, `python attachments/foo.py`, `python /tmp/foo.py` | ❌ blocked (Write+exec workaround closed) |
| `python` (REPL), `python -m <anything>`, `python -i ...`, `python -V`, `python < /dev/stdin`, `cat foo.py \| python` | ❌ blocked |

Stdlib whitelist for `python -c` imports: `math`, `statistics`,
`decimal`, `fractions`, `json`, `re`, `datetime`, `random`,
`itertools`, `functools`, `collections`, `string`, `textwrap`,
`unicodedata`, `base64`, `hashlib`, `uuid`, `time`, `operator`,
`copy`, `enum`, `typing`. Anything else needs to live as a registered
skill — that's the trust passport.

**Skill = capability bounded.** The dev decides "my agent can query
Qdrant" by writing a `query-qdrant` skill with its own
`scripts/search.py`. The agent runs that script through the
skill-script whitelist; it cannot import `qdrant_client` directly via
`python -c`. Strict stays strict — no flag explosion needed.

In permissive mode the python-c whitelist does **not** apply: the dev
has signed off on trusted-tool tier and any `python` call is fine
(other than the cross-mode escapes like `bash -c`, `eval`, `/dev/tcp`,
`| python`, ...).

---

## What the motor still does NOT control

- **Skill scripts are trusted code** (yours). The motor symlinks
  whatever you put under `default_skills` into the run. If a skill's
  `scripts/foo.py` does something destructive, the guard won't catch
  it — the dev who registered the skill has signed off on it.
- **The `Skill` tool itself is a code-execution surface** by design.
  Strip it from `tools` if your trust boundary doesn't include
  whoever wrote the skills.
- **`guardrail="off"`** is opt-in escape hatch. Use only inside an
  ephemeral container or a dedicated VM where blast radius is the
  container itself.
- **Determined evasion** via heavy obfuscation (custom encoding +
  `compile()` chains, ctype tricks via skills, etc.) is still
  possible. The guard defeats the common prompt-injection and
  honest-mistake cases — it is not a formal sandbox.
- **Other interpreters** beyond Python (`lua`, `tcl`, `julia`, `R`,
  `php -r`, `awk 'BEGIN{system(...)}'`, `sed 'e ...'`, future
  runtimes) are not all individually parsed. The blocklist catches
  the common ones (`node`, `ruby`, `perl`, `pwsh`); rare/exotic
  interpreters can slip through if you make them available in `PATH`.
  The guard is a **lexical first filter**, not an exhaustive runtime
  registry.

---

## Production hardening

The strict guard catches the common LLM mistake and the naïve prompt-
injection. It is **not** a sandbox you can rely on alone. For anything
that touches real users or real secrets, layer OS-level isolation
underneath:

```
Container (Docker, k8s, Firecracker, ...)
  └─ non-privileged user (UID ≥ 1000), no sudo, no setuid bits
     └─ read-only filesystem, except /data (volume) and /tmp (tmpfs)
        └─ no outbound network (or NetworkPolicy / iptables egress allowlist)
           └─ dropped Linux capabilities (--cap-drop=ALL, then add only what's needed)
              └─ resource limits (--memory, --cpus, --pids-limit)
                 └─ then the motor with guardrail="strict"
```

Each layer covers a different threat:

| Layer | What it stops | Without it... |
|---|---|---|
| Non-priv user | `sudo`, `chmod` on system files, mount, kill other processes | guard's `sudo` block isn't enough — root can still escape |
| Read-only FS | `Write`/`shutil.rmtree` on system paths, planted persistent files | guard restricts `Write` to `outputs/` but a bug = host damage |
| No outbound network | Exfiltration of secrets the env strip didn't catch | env strip is best-effort, network gate is binary |
| Dropped capabilities | `mount`, `setuid`, raw socket | `CAP_NET_RAW` would let the agent skip our network gate |
| Resource limits | Fork bombs, CPU/memory exhaustion DoS | the guard doesn't measure resource usage |

For an `examples/docker/` starting point with most of these baked in,
see [examples/docker/](../examples/docker/). For Kubernetes, use a
`securityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`,
`capabilities.drop: [ALL]`) and a `NetworkPolicy` denying egress.

The guard saves you from the easy 95%. The OS layer is what keeps the
remaining 5% from blowing up. **Use both — you need both.**
