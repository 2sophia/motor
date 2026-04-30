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
  input.json           # parametri di input + config snapshot
  scratch/             # file da seedare con task.cwd_files
  outputs/             # file generati dall'agent (Write tool va qui)
  audit/
    request_001.json   # body POST /v1/messages
    response_001.json  # body response (o .sse per stream)
    request_002.json
    ...
  trace.json           # blocks finali + metadata
```

`run_id = run-<timestamp>-<8 hex>`. Workspace persistente — non viene cancellato a fine run (è il file system di audit). Per cleanup: cron o policy decisi a livello deployment.

## Defaults del MotorConfig

| Campo | Default | Note |
|---|---|---|
| `model` | `claude-opus-4-6` | scelto da Alex il 2026-04-30 — Opus 4.6 è il modello compliance di riferimento |
| `upstream_base_url` | `https://api.anthropic.com` | il proxy forwarda qui |
| `api_key` | da `ANTHROPIC_API_KEY` env | obbligatorio per chiamate vere |
| `workspace_root` | `./.runs` | risolto absolute in __init__ |
| `proxy_enabled` | `True` | NON disabilitare in produzione |
| `proxy_dump_payloads` | `True` | richiesto per BdI defense |
| `proxy_strip_sdk_noise` | `True` | risparmia token su ogni request |
| `console_log_enabled` | `True` | colorato, opt-out per silence |
| `default_max_turns` | `20` | hard cap, override per RunTask |
| `default_timeout_seconds` | `300` | non ancora wired in run() |

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
        system_prompt="...",          # optional
        allowed_tools=["Read"],        # whitelist core SDK tools
        disallowed_tools=[],           # blacklist
        max_turns=10,
        cwd_files={"scratch/note.txt": "..."},
    ))

    # result.run_id          str
    # result.output_text     str | None  (final assistant text)
    # result.blocks          list[dict]  (every text/thinking/tool_use/tool_result)
    # result.metadata        RunMetadata (turns, tokens, cost, duration, is_error)
    # result.audit_dir       Path        (dove vivono request_*.json / response_*.json)
    # result.workspace_dir   Path        (run dir intera)
```

## Cosa NON c'è ancora (roadmap)

Stato: **PoC Fase 0 verificato sul lifecycle proxy**. Cosa manca per Fase 1 → ProgramRun strutturato:

- [ ] **Run end-to-end con Opus reale** (serve `ANTHROPIC_API_KEY`)
- [ ] **`AgentProgram` dichiarativo** (system_prompt + allowed_tools + output_schema Pydantic + max_turns + budget)
  - `motor.run_program(program, inputs)` → output Pydantic-conforming
- [ ] **Custom tool / skill catalog** (pattern delle skill Sophia: SKILL.md + scripts/)
  - Prima skill: `read_obligation` come riferimento
- [ ] **Output schema strict** (JSON mode forzato + retry + fallback deterministico)
- [ ] **Cost budget enforcement** (config + circuit breaker)
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
