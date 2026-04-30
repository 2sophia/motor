"""Playground — il file minimal che farà l'utente.

Setup richiesto (una volta sola):
  1. ANTHROPIC_API_KEY in env, oppure dentro `./.env` (auto-letto)
  2. .venv del pacchetto attivo o presente

Lancia:
  cd /home/mwspace/htdocs/sophia-motor
  .venv/bin/python examples/playground.py

Modifica le sezioni 👇 TODO 👇 per provare task diversi. Il motor:
  - boota un proxy locale sotto il cofano
  - persiste ogni request/response in `.runs/<run_id>/audit/`
  - stampa eventi e log a console (cyan/magenta)
  - blinda i tool secondo `tools=` / `disallowed_tools=`
  - copia/scrive gli attachments in `.runs/<run_id>/attachments/`
  - ritorna RunResult con output_text + blocks + metadata + audit_dir

Per pulire i runs accumulati:
  from sophia_motor import clean_runs
  clean_runs(".runs", keep_last=5)        # tieni gli ultimi 5
  clean_runs(".runs", older_than_days=7)  # rimuovi più vecchi di 7gg
  clean_runs(".runs")                     # rimuovi tutto
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sophia_motor import Motor, MotorConfig, RunTask


# ── 👇 TODO: il prompt che l'agent deve eseguire ────────────────────────
PROMPT = (
    "Leggi il file `attachments/data.txt` e produci un riassunto di 2-3 "
    "frasi in italiano sui contenuti normativi citati."
)

# ── 👇 TODO: attachments — cosa l'agent troverà sotto attachments/ ──────
# Lista polimorfa, mix libero. Tre forme accettate:
#
#   1. Path / str → file reale  → copiato in attachments/<filename>
#        Path("/data/regulation.pdf")  → attachments/regulation.pdf
#
#   2. Path / str → directory reale → copiata ricorsivamente
#        Path("/data/policy_dir")  → attachments/policy_dir/...
#
#   3. dict[str, str] → file inline {relpath: contenuto}
#        {"data.txt": "..."}              → attachments/data.txt
#        {"sub/note.txt": "..."}          → attachments/sub/note.txt
#
# Pre-flight check automatico — se manca o non è leggibile, raise
# PRIMA di consumare token.
ATTACHMENTS: list = [
    {
        "data.txt": (
            "ViViBanca pubblica trimestralmente i tassi soglia ai sensi "
            "della legge 108/1996. Il tasso per il primo trimestre 2026 "
            "è 12.5% per il credito al consumo. La policy interna "
            "PRGN000007 impone l'aggiornamento dei contratti entro 15 "
            "giorni dalla pubblicazione del decreto MEF."
        ),
    },
    # esempio path reale (decommenta e cambia con un tuo PDF/MD reale):
    # Path("/home/mwspace/htdocs/rgci-intelligence/.data/some_doc.pdf"),
]

# ── 👇 TODO: tool che il modello può usare (HARD whitelist) ─────────────
# Esempi:
#   ["Read"]                  → solo lettura file
#   ["Read", "Grep", "Glob"]  → lettura + ricerca
#   ["Read", "Bash"]          → con shell (attenzione)
#   None                      → tutti i tool default SDK (sconsigliato)
TOOLS: list[str] | None = ["Read"]

# ── 👇 TODO: system prompt opzionale ────────────────────────────────────
# Lascia None per il default SDK.
SYSTEM: str | None = (
    "You are a compliance reasoning agent. Use relative paths, never "
    "absolute. Be concise."
)


async def main() -> None:
    # MotorConfig() = tutto auto:
    #   - api_key da env o ./.env
    #   - model = claude-opus-4-6
    #   - workspace_root = ./.runs
    #   - proxy + audit_dump + console_log: ON
    #   - default_disallowed_tools = sane defaults (no web, no agent spawn, ...)
    config = MotorConfig()

    async with Motor(config) as motor:
        # (opzionale) subscriber custom in aggiunta alla console default:
        #
        # @motor.on_event
        # async def my_handler(event):
        #     if event.type == "tool_use":
        #         print(f"[mio handler] tool: {event.payload['tool']}")

        result = await motor.run(RunTask(
            prompt=PROMPT,
            system=SYSTEM,
            tools=TOOLS,
            allowed_tools=TOOLS,
            attachments=ATTACHMENTS,
            max_turns=10,
        ))

    # ── output ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"run_id     : {result.run_id}")
    print(f"is_error   : {result.metadata.is_error}")
    print(f"turns      : {result.metadata.n_turns}")
    print(f"tool_calls : {result.metadata.n_tool_calls}")
    print(f"tokens     : in={result.metadata.input_tokens} "
          f"out={result.metadata.output_tokens}")
    print(f"cost       : ${result.metadata.total_cost_usd:.4f}")
    print(f"duration   : {result.metadata.duration_s:.1f}s")
    print(f"audit dir  : {result.audit_dir}")
    print(f"workspace  : {result.workspace_dir}")
    print("=" * 60)
    print(f"\nOUTPUT\n{result.output_text or '(empty)'}\n")


if __name__ == "__main__":
    asyncio.run(main())
