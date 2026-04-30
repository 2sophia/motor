# CLAUDE.md

Guidance per Claude Code (Eco) che lavora su `sophia-motor`. Leggimi prima di toccare codice.

## Cos'è

`sophia-motor` è un **motor agent instanziabile e programmabile** che wrappa il Claude Agent SDK e mette davanti un proxy HTTP locale. È un pacchetto Python pip-installable, MIT-licensed, riusabile come building block da Sophia, RGCI e futuri prodotti che hanno bisogno di "agent sotto il cofano".

In sintesi: **prendi il Claude Agent SDK, lo metti in subprocess via class-based API con event hooks, lo controlli via proxy gateway con audit dump per la difesa BdI**.

## Perché esiste

Il bisogno è nato lavorando su RGCI (`/home/mwspace/htdocs/rgci-intelligence`). Il verdict gap analysis di RGCI è un single-shot LLM call su pool di 30 controlli — funziona ma è statico, non riproducibile bit-per-bit, non difendibile davanti a BdI come "ragionamento dimostrato".

L'idea: fare il verdict come **agent** (legge controlli uno a uno, cita verbatim, ragiona iterativamente, cross-reference altre fonti). Ma serve un motor:
- **instanziabile**: stesso pattern per N programmi (verdict, cross-reference, gazzetta-lookup, dialog…)
- **controllabile**: binari deterministici (max_turns, budget, output schema, scope FS/rete)
- **auditabile**: ogni request/response/decisione persistita
- **riusabile**: Sophia lo userà per i compliance-officer dialogs, RGCI per i verdict, futuri prodotti per altro

Sophia ha già il pattern (vedi `/home/mwspace/htdocs/sophia-agent/app/services/agent_service.py` e `app/api/routes/anthropic_proxy.py`). `sophia-motor` lo **astrae e generalizza** in pacchetto.

## Dove

- **Repo**: `/home/mwspace/htdocs/sophia-motor/` (separato, non dentro RGCI né Sophia)
- **Branch**: `main` (PoC iniziale, niente branching strategy ancora)
- **Pacchetto Python**: `sophia_motor` (snake_case, layout `src/`)
- **Versione**: `0.0.1` pre-alpha — aspettati breaking changes

## Architettura — i 3 pezzi che contano

```
┌──────────────────────────────────────────────────────────┐
│  Caller (RGCI / Sophia / test code)                      │
│   async with Motor(config) as motor:                     │
│       motor.on_event(handler)                            │
│       result = await motor.run(RunTask(...))             │
└──────────────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│  Motor  (src/sophia_motor/motor.py)                      │
│  - context-managed lifecycle                             │
│  - boota il proxy in __aenter__, lo ferma in __aexit__   │
│  - run() = setup workspace + build SDK opts + esegui     │
│  - emette eventi turn-by-turn via EventBus               │
└──────────────────────────────────────────────────────────┘
         │                                       │
         │ ClaudeSDKClient (subprocess)          │ events/log
         ▼                                       │
┌─────────────────────────┐                      │
│  Claude CLI subprocess  │                      │
│  (binary del SDK)       │                      │
└─────────────────────────┘                      │
         │ HTTP  (ANTHROPIC_BASE_URL)            │
         ▼                                       │
┌──────────────────────────────────────────────────────────┐
│  ProxyServer  (src/sophia_motor/proxy.py)                │
│  FastAPI in-process su uvicorn, porta libera             │
│  POST /v1/messages:                                      │
│   1. strip SDK noise (billing + identity)                │
│   2. dump request JSON in <run>/audit/                   │
│   3. emit proxy_request event                            │
│   4. forward → upstream Anthropic API                    │
│   5. dump response, emit proxy_response event            │
└──────────────────────────────────────────────────────────┘
                         │
                         ▼
                  https://api.anthropic.com
```

**Tre concetti chiave**:

1. **Motor = una istanza**. Una `Motor(config)` gestisce un run alla volta. Per parallelismo: N istanze in parallelo (ognuna con il suo proxy su porta diversa). NON tentare di forzare N run paralleli sulla stessa istanza — il proxy è bound al `_current_run_id` per il dump tagging.

2. **Proxy = load-bearing, non debug**. È l'unico posto dove possiamo intercettare i messaggi tra SDK subprocess e Anthropic. Serve per audit dump, eventi turn-by-turn, strip noise, e (futuro) cost tracker / schema preflight / prompt cache marker / circuit breaker. Disabilitabile (`proxy_enabled=False`) solo per unit test che mockano il SDK.

3. **EventBus = stream di telemetria**. Due stream: `Event` (strutturati) e `LogRecord` (livellati). Subscriber sync o async, registrabili come decorator (`@motor.on_event`) o chiamata diretta (`motor.on_event(fn)`). Errori in subscriber non killano il motor.

## Workspace per run

Ogni `motor.run(task)` crea:

```
<workspace_root>/<run_id>/
  input.json           # parametri input + config snapshot + manifest attachments+skills
  trace.json           # blocks finali + metadata
  audit/
    request_001.json   # body POST /v1/messages
    response_001.sse   # response stream (.sse) o .json (sync)
    ...
  .claude/             # CLAUDE_CONFIG_DIR — sibling di agent_cwd, non child
    plugins/           # stub installed_plugins.json (no plugin loading)
    skills/            # symlink alle skill abilitate (multi-source supported)
    backups/, sessions/   # state CLI (auto)
  agent_cwd/           # cwd del subprocess (sandbox)
    attachments/       # symlink ai Path passati + file inline scritti
    outputs/           # file generati dall'agent (Write tool va qui)
```

`run_id = run-<timestamp>-<8 hex>`. Workspace persistente — non viene cancellato a fine run (è il file system di audit). Per cleanup: `motor.clean_runs(...)` o `clean_runs(workspace_root, ...)`.

**Why two levels (root + agent_cwd):** il root è motor-owned (audit, trace, input, .claude config). `agent_cwd/` è la **sandbox** del subprocess CLI: l'agent vede solo `attachments/` e `outputs/`, mai `audit/` o `.claude/` interno. Layout BdI-grade: l'auditor leggendo il root capisce subito cosa è motor vs cosa è agent.

### Attachments — singolo o lista, link by default

`RunTask.attachments: Path | str | dict | list[...]`. Polimorfo per ergonomia: passa direttamente un Path se hai un solo input, una lista se ne hai più.

| Forma | Esempio | Risultato |
|---|---|---|
| `Path` di FILE reale | `Path("/data/reg.pdf")` | **symlink** `attachments/reg.pdf` → `/data/reg.pdf` |
| `Path` di DIRECTORY reale | `Path("/data/policy/")` | **symlink** `attachments/policy` → `/data/policy/` |
| `dict[str, str]` inline | `{"note.txt": "ciao"}` | file vero `attachments/note.txt` |
| dict con sub-path | `{"sub/note.txt": "x"}` | file vero `attachments/sub/note.txt` |
| singolo `Path` | `attachments=Path("/data/")` | normalizzato a `[Path("/data/")]` |
| lista mista | `[Path(...), Path(...), {"x.txt":"y"}]` | tutti materializzati insieme |

**Default = symlink** per i Path (no copia, no storage waste, no duplicazione). L'audit BdI passa per gli SSE in `audit/`, non per il filesystem in `attachments/`. Il dump dei tool_result registra il TESTO che il modello ha letto.

**Pre-flight check** (errore prima di consumare token):
- path mancante → `FileNotFoundError`
- non file né dir → `ValueError`
- non leggibile → `PermissionError`
- dict key absolute o con `..` → `ValueError`
- dict value non-str → `TypeError`
- due item che destinano allo stesso path → `ValueError` (conflitto)

### Skills — singolo o lista di folder source

`RunTask.skills: Path | str | list[Path | str] | None`. Anche qui polimorfo.

Ogni source è una **folder** che contiene skill (subdir con `SKILL.md`). Il motor:
- itera le subdir di ogni source
- per ognuna che ha `SKILL.md` e non è in `disallowed_skills`
- crea symlink in `<run>/.claude/skills/<skill_name>/` → source skill

Multi-source supportato: la stessa lista può contenere più folder (es. skill specifiche del programma + skill condivise org-wide). **Conflict di nome tra source** → `ValueError` con messaggio chiaro.

```python
RunTask(
    skills=Path("./skills/"),                    # singola folder
    # oppure:
    skills=[
        Path("./program/skills/"),
        Path("./org/shared-skills/"),
    ],
    disallowed_skills=["heavy-skill"],           # opt-out specifici
)
```

`CLAUDE_CONFIG_DIR=<run>/.claude/` viene impostato nell'env del SDK, così il CLI subprocess carica le skill linkate.

**`input.json` registra entrambi i manifest** (attachments + skills). Per ogni voce: `<inline>` per file scritti inline, `→ <abs_path> (link)` per simlink. Audit defense: per ogni run sai esattamente cosa il modello aveva a disposizione.

## Defaults del MotorConfig

| Campo | Default | Note |
|---|---|---|
| `model` | `claude-opus-4-6` | scelto da Alex il 2026-04-30 — Opus 4.6 è il modello compliance di riferimento |
| `upstream_base_url` | `https://api.anthropic.com` | il proxy forwarda qui |
| `api_key` | da `ANTHROPIC_API_KEY` env | obbligatorio per chiamate vere |
| `workspace_root` | `~/.sophia-motor/runs/` | **MUST be outside any repo** — vedi sezione "CLI quirks" |
| `disable_claude_md` | `True` | inibisce auto-load di Project/Local CLAUDE.md (env `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`) |
| `proxy_enabled` | `True` | NON disabilitare in produzione |
| `proxy_dump_payloads` | `True` | richiesto per BdI defense |
| `proxy_strip_sdk_noise` | `True` | risparmia token su ogni request |
| `proxy_strip_user_system_reminders` | `True` | strip `<system-reminder>` dai messaggi user dal turn 2 |
| `console_log_enabled` | `True` | colorato, opt-out per silence |
| `default_max_turns` | `20` | hard cap, override per RunTask |
| `default_timeout_seconds` | `300` | non ancora wired in run() |
| `cli_no_session_persistence` | `True` | passa `--no-session-persistence` al CLI |

## CLI quirks — cose imparate empiricamente

Il binary del Claude Agent SDK (`.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude`, bun-bundled, opaco) ha comportamenti non documentati che il motor neutralizza.

### Quirk 1 — Project root upward discovery

Il CLI sale dalla cwd cercando marker (`.git/`, `pyproject.toml`, `package.json`). Se ne trova uno upward, **rewrita le path di session/backup** in un fallback path nidificato `<cwd>/<rel-to-discovered-root>/.claude/{backups,sessions}` invece di usare `CLAUDE_CONFIG_DIR`. Verificato empiricamente:

- Workspace `/home/mwspace/htdocs/sophia-motor/.runs/<RID>/agent_cwd/` (dentro repo) → nidificazione `agent_cwd/.runs/<RID>/agent_cwd/.claude/...` ❌
- Workspace `/tmp/sophia-motor-test/<RID>/agent_cwd/` (no marker upward) → CLI scrive in `<run>/.claude/` come previsto ✓

Tentativi di override **falliti** (testati):
- `CLAUDE_PROJECT_DIR=<agent_cwd>` env var
- `git init` in `agent_cwd/`
- File `.git` (vuoto) in `<run>/`
- `.git/` directory minima in `<run>/`
- Nome workspace senza dot-prefix (`runs/` vs `.runs/`)

**Unico fix affidabile**: `workspace_root` deve avere ancestor "puliti" (senza `.git/`, `pyproject.toml`, `package.json`). Default `~/.sophia-motor/runs/` è sempre safe. In container: passa `MotorConfig(workspace_root="/data/runs")` con volume montato.

### Quirk 2 — CLAUDE_CONFIG_DIR ignorato senza pre-seed

Il CLI ignora `CLAUDE_CONFIG_DIR` env se `<config_dir>/plugins/installed_plugins.json` non esiste. Il motor pre-crea quel file in `_seed_claude_config_dir`. Senza il seed, il CLI fa fallback alla path autoderivata (vedi quirk 1).

### Quirk 3 — Env vars di disable native

Il binary risponde a una decina di env var che disabilitano comportamenti default (telemetry, title-gen, auto-memory, file checkpointing, ecc.). Il motor le imposta tutte:

| Env var | Effetto |
|---|---|
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | telemetry, title-generation, onboarding-prompt |
| `CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING` | non scrive `projects/<encoded-cwd>/<session>.jsonl` |
| `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | no sub-spawn |
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | no auto-memory feature |
| `CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY` | no survey |
| `CLAUDE_CODE_DISABLE_TERMINAL_TITLE` | no terminal title rewrite |
| `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS` | no auto-suggerimenti git (commit, branch, ecc.) |
| `CLAUDE_CODE_DISABLE_CLAUDE_MDS` | no auto-load Project/Local CLAUDE.md (gated da `disable_claude_md`) |
| `DISABLE_TELEMETRY`, `_ERROR_REPORTING`, `_AUTOUPDATER`, `_AUTO_COMPACT`, `_BUG_COMMAND` | generic anti-noise |
| `ENABLE_TOOL_SEARCH=false` | tutti i tool caricati upfront, no deferred ToolSearch dance |

Effetto cumulativo (verificato):
- Cost test skill say-hello: $0.0187 → $0.0162 (-14%)
- Durata: 8.0s → 5.8s (-27%)
- 1 HTTP request risparmiato (warm-up sparito)
- Niente `projects/` nidificato

### Quirk 4 — `--bare` rompe le skill

Tentazione: passare `--bare` per output minimal. **NON FARLO**. In bare mode le skill diventano slash-command (`/skill-name`) invece di Skill tool, e il modello smette di invocarle via tool_use. Output diventa una stringa letterale `<tool_call>...`. Default `cli_bare_mode=False`, on solo per casi senza skill.

### Quirk 5 — `setting_sources` deve essere `["project"]`

Provato `["local"]` → il CLI smette di trovare le skill linkate in `<config_dir>/skills/`. `["project"]` è l'unica scelta che fa funzionare la skill discovery via CLAUDE_CONFIG_DIR.

## Comandi

### Venv

Setup one-time (richiede `python3.12-venv` di sistema, già installato):

```bash
cd /home/mwspace/htdocs/sophia-motor
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Da qui in poi tutto gira via `.venv/bin/python` o `.venv/bin/pytest`.

### Quick playground (per provare task custom)

```bash
cd /home/mwspace/htdocs/sophia-motor

# api key in ./.env oppure esportata in shell
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

.venv/bin/python examples/playground.py
```

`examples/playground.py` ha 3 sezioni TODO da editare: prompt, file da seedare,
tool whitelist. Tutto il resto (proxy, audit dump, console log) è auto via
`MotorConfig()`.

### Cleanup runs accumulati

```bash
.venv/bin/python examples/clean.py     # default: tieni gli ultimi 5
```

In codice:
```python
from sophia_motor import clean_runs
clean_runs(".runs", keep_last=5)        # tieni gli ultimi 5
clean_runs(".runs", older_than_days=7)  # rimuovi >7gg
clean_runs(".runs")                     # rimuovi tutto
clean_runs(".runs", dry_run=True)       # solo lista
```

Oppure bound al motor:
```python
async with Motor(config) as motor:
    motor.clean_runs(keep_last=10)
```

### Run smoke test (legacy interno)

```bash
cd /home/mwspace/htdocs/sophia-motor
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python tests/run_smoke.py
```

Output atteso:
- log colorato cyan/magenta turn-by-turn
- `proxy_request` → `assistant_text` (eventuale `tool_use` + `tool_result`) → `proxy_response` → `result`
- audit dir popolata con `request_001.json` e `response_001.json`

### Run pytest

```bash
cd /home/mwspace/htdocs/sophia-motor
.venv/bin/pytest tests/ -v
```

Note:
- `test_motor_starts_proxy_and_stops_clean` non chiama il modello, sempre eseguibile (~500ms)
- `test_motor_runs_simple_read_task` skippa se `ANTHROPIC_API_KEY` manca

### Debug rapido (lifecycle proxy senza modello)

```bash
cd /home/mwspace/htdocs/sophia-motor
.venv/bin/python -c "
import asyncio
from sophia_motor import Motor, MotorConfig

async def main():
    cfg = MotorConfig(api_key='dummy')
    async with Motor(cfg) as motor:
        print(f'proxy: {motor._proxy.base_url}')
asyncio.run(main())
"
```

## API pubblica (ergonomica)

```python
from sophia_motor import Motor, MotorConfig, RunTask

config = MotorConfig(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    model="claude-opus-4-6",
    workspace_root="./.runs",
)

async with Motor(config) as motor:
    @motor.on_event
    async def on_event(event):
        # event.type: "run_started", "tool_use", "tool_result",
        #             "assistant_text", "thinking", "proxy_request",
        #             "proxy_response", "result"
        ...

    @motor.on_log
    async def on_log(record):
        # record.level: "DEBUG" | "INFO" | "WARNING" | "ERROR"
        ...

    result = await motor.run(RunTask(
        prompt="...",
        system="...",                       # optional system prompt
        tools=["Read", "Skill"],            # HARD whitelist
        allowed_tools=["Read", "Skill"],    # auto-allow
        max_turns=10,
        # attachments: singolo Path, oppure lista mista
        attachments=Path("/data/"),
        # skills: singolo folder source, oppure lista
        skills=Path("./skills/"),
        disallowed_skills=["heavy-one"],
    ))

    # result.run_id          str
    # result.output_text     str | None  (final assistant text)
    # result.blocks          list[dict]  (every text/thinking/tool_use/tool_result)
    # result.metadata        RunMetadata (turns, tokens, cost, duration, is_error)
    # result.audit_dir       Path        (dove vivono request_*.json / response_*.json)
    # result.workspace_dir   Path        (run dir intera)
```

## Cosa NON c'è ancora (roadmap)

Stato: **PoC Fase 0 + 1a verificato** (lifecycle proxy + run end-to-end Opus 4.6 + skill linking + audit dump). Cosa manca per Fase 1 completa → ProgramRun strutturato:

- [x] ~~Run end-to-end con Opus reale~~ — verificato 2026-04-30, skill `say-hello` end-to-end
- [x] ~~Tool whitelist semantics~~ — `tools=` hard, `allowed_tools=` permission, `disallowed_tools=` block
- [x] ~~CLI subprocess hardening~~ — env disable + quirks doc (vedi sezione "CLI quirks")
- [ ] **Output schema strict** (JSON mode forzato via `--json-schema` flag native + Pydantic validation + retry self-correcting)
- [ ] **`AgentProgram` dichiarativo** (system_prompt + allowed_tools + output_schema Pydantic + max_turns + budget)
  - `motor.run_program(program, inputs)` → output Pydantic-conforming
- [ ] **Custom tool / skill catalog** (pattern delle skill Sophia: SKILL.md + scripts/)
  - Prima skill: `read_obligation` come riferimento
- [ ] **Cost budget enforcement** (config + circuit breaker, env `--max-budget-usd` native)
- [ ] **Prompt cache marker** (`cache_control: ephemeral` sul system prompt)
- [ ] **Schema preflight** (proxy blocca tool fuori catalogo del programma)
- [ ] **Drift detector** (cron — ri-esegue 5 verdict gold, alert se differisce)
- [ ] **Eval suite** (pattern `eval/` di sophia, gold da `compliance_feedback`)

## Cosa NON deve toccare il motor

- **Sophia agent** (`/home/mwspace/htdocs/sophia-agent/`): rimane intatto. Quando Sophia migrerà sul motor, lo farà esplicitamente. Per ora sophia-motor è solo nuovo codice.
- **RGCI verdict pipeline** (`/home/mwspace/htdocs/rgci-intelligence/app/services/gap_analysis/`): il path attuale non si tocca. L'integrazione futura passerà via flag `RGCI_VERDICT_MODE = static_llm | agent`, niente big-bang.

## Convenzioni Alex

- Chiede conferma esplicita per **modifiche di stato** (sudo, install, systemctl, write su `/etc`, git push, rm)
- Per **read-only** (ls, cat, grep, git status/diff/log) parte subito
- **Italiano informale** in chat e commit, **inglese** in codice/docs
- **Mini-piano 3 punti pre-action**, poi parte
- **Niente summary lunghi** a fine turn — 1-2 frasi *"fatto X e Y, prossimo?"*
- **Test passo passo** prima di nuove decisioni — non build big bang
- **Root cause > workaround**, sempre

### ⚠️ Workspace personale — NON CANCELLARE

Alex tiene file di lavoro in `~/.sophia-motor/`. Quando devi pulire i runs:

```bash
# OK ✓ — pulisci solo i runs
rm -rf ~/.sophia-motor/runs/*

# NO ❌ — cancella tutto incluso ciò che Alex tiene lì
rm -rf ~/.sophia-motor
```

In codice: `motor.clean_runs()` o `clean_runs(workspace_root, ...)` — entrambi lavorano dentro `runs/`, mai sopra.

## Provenienza dei pattern

Ogni pezzo non triviale viene da Sophia. Quando dubiti, vai a guardare l'originale:

| Pezzo `sophia-motor` | Pattern originale Sophia |
|---|---|
| `proxy.py` strip SDK noise | `app/api/routes/anthropic_proxy.py:_strip_sdk_system_blocks` |
| `proxy.py` dump payloads | `_dump_payload` (estesa: in Sophia era debug-gated, qui sempre attiva) |
| `motor.py` SDK options building | `app/services/agent_service.py:_build_opts` (ridotto: niente skill sync, niente per-chat workspace, solo per-run) |
| `motor.py` SDK message dispatch | `agent_service.py:send_message` + `stream_message` (semplificato: collect-only per ora) |
| `events.py` EventBus | nuovo, ma sostituisce il pattern logger-disperso di Sophia con uno stream-based |

## Riferimenti

- Claude Agent SDK: <https://github.com/anthropics/claude-agent-sdk-python>
- Sophia agent codebase: `/home/mwspace/htdocs/sophia-agent/CLAUDE.md`
- RGCI codebase: `/home/mwspace/htdocs/rgci-intelligence/CLAUDE.md`
- Pattern di lavoro Eco/Alex: `~/.claude/CLAUDE.md` (globale)
