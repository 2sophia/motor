"""Standalone smoke test (non-pytest).

Run with sophia-agent's venv until we have a dedicated one:

    cd /home/mwspace/htdocs/sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... \\
      /home/mwspace/htdocs/sophia-agent/.venv/bin/python tests/run_smoke.py

You will see the live event/log stream on stdout and an audit dir under
./.runs/<run_id>/ when the run completes.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make `import sophia_motor` work without `pip install -e .`
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sophia_motor import Motor, MotorConfig, RunTask  # noqa: E402


SAMPLE_TEXT = """\
La banca ViViBanca pubblica trimestralmente i tassi soglia dell'usura ai sensi
dell'articolo 2 della legge 108/1996. Il tasso soglia per il primo trimestre 2026
è fissato al 12.5% per il credito al consumo. La policy interna PRGN000007
prevede l'aggiornamento dei contratti entro 15 giorni dalla pubblicazione del
decreto MEF.
"""


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: ANTHROPIC_API_KEY non impostato.\n"
            "       Esporta la chiave prima di lanciare il test."
        )
        return 2

    config = MotorConfig(
        workspace_root=Path("./.runs"),
        console_log_enabled=True,
    )

    print("\n=== sophia-motor smoke test ===\n")

    async with Motor(config) as motor:
        result = await motor.run(RunTask(
            prompt=(
                "Leggi il file `scratch/sample.txt` (path relativo alla tua "
                "working directory) e produci un breve riassunto in 2 frasi "
                "sui contenuti normativi citati. Rispondi in italiano."
            ),
            system_prompt=(
                "You are a compliance reasoning agent. All file paths you use "
                "MUST be relative to the current working directory. Never use "
                "absolute paths."
            ),
            tools=["Read"],          # ← HARD whitelist: solo Read, niente altro
            allowed_tools=["Read"],  # ← skip permission prompt
            cwd_files={"scratch/sample.txt": SAMPLE_TEXT},
            max_turns=5,
        ))

    print("\n=== RESULT ===")
    print(f"run_id:      {result.run_id}")
    print(f"is_error:    {result.metadata.is_error}")
    print(f"turns:       {result.metadata.n_turns}")
    print(f"tool_calls:  {result.metadata.n_tool_calls}")
    print(f"tokens:      in={result.metadata.input_tokens} "
          f"out={result.metadata.output_tokens}")
    print(f"cost:        ${result.metadata.total_cost_usd:.4f}")
    print(f"duration:    {result.metadata.duration_s:.1f}s")
    print(f"audit dir:   {result.audit_dir}")
    print(f"workspace:   {result.workspace_dir}")
    print(f"\nOutput:\n{result.output_text or '(none)'}\n")

    return 0 if not result.metadata.is_error else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
