"""Minimal verdict example — il pattern d'uso "happy path" di Sophia Motor.

Stesso `motor` istanziato UNA volta a livello modulo, riusato per N task
che variano solo per il `prompt`. Lazy auto-start del proxy al primo run
(niente `async with`, niente lifecycle ceremony).

Lancia:
  cd /home/mwspace/htdocs/sophia-motor
  ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python examples/verdict_minimal.py
"""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from sophia_motor import Motor, MotorConfig, RunTask


# ── 1) Schema dell'output (Pydantic). Qualunque BaseModel valido. ────────
class Verdict(BaseModel):
    verdetto: Literal["ALTA", "MEDIA", "BASSA"]
    motivazione: str = Field(min_length=20)
    sub_req_coperti: list[str]
    sub_req_non_coperti: list[str]


# ── 2) Istanzia Motor UNA volta a livello modulo, con i default comuni.
#    Il dev può usarlo da qualunque async function del progetto.
#    Niente `async with`: il proxy parte alla prima `motor.run()`.
motor = Motor(MotorConfig(
    default_system="Sei un compliance officer di una banca italiana.",
    default_output_schema=Verdict,
    default_tools=[],            # puro reasoning, no tool a default
    default_allowed_tools=[],
    default_max_turns=5,
))


# ── 3) Una "funzione intelligente" è una normale async def Python che
#    costruisce un RunTask col solo campo che varia (qui: il prompt).
async def assess_obligation(obligation_text: str, controls: list[str]) -> Verdict:
    """Valuta la copertura di un obbligo dato un set di controlli candidati."""
    task = RunTask(
        prompt=(
            f"Valuta se l'obbligo è coperto dai controlli candidati.\n"
            f"Decomponi in sub-requirement, cita verbatim, produci verdetto.\n\n"
            f"OBBLIGO:\n{obligation_text}\n\n"
            f"CONTROLLI CANDIDATI:\n"
            + "\n".join(f"- {c}" for c in controls)
        ),
        # niente system/tools/output_schema/max_turns: vengono dai default
        # del MotorConfig sopra. Si potrebbero override qui per il singolo task.
    )
    result = await motor.run(task)
    if result.metadata.is_error:
        raise RuntimeError(f"verdict failed: {result.metadata.error_reason}")
    return result.output_data  # type: ignore[return-value]


async def main() -> None:
    # Prima chiamata: il proxy si avvia trasparente (~500ms una volta sola).
    v1 = await assess_obligation(
        obligation_text=(
            "L'organo di controllo verifica entro 30 giorni dalla pubblicazione "
            "il superamento dei tassi soglia ai sensi della legge 108/1996."
        ),
        controls=[
            "CTRL-001: Verifica trimestrale dei tassi soglia (Risk Mgmt)",
            "CTRL-042: Audit annuale di conformità (Internal Audit)",
        ],
    )
    print(f"=== Verdict 1 ===")
    print(f"  verdetto    : {v1.verdetto}")
    print(f"  motivazione : {v1.motivazione}")
    print(f"  coperti     : {v1.sub_req_coperti}")
    print(f"  non coperti : {v1.sub_req_non_coperti}")

    # Seconda chiamata: proxy già vivo, dispatch immediato.
    v2 = await assess_obligation(
        obligation_text=(
            "La banca pubblica trimestralmente i tassi soglia sul proprio sito web."
        ),
        controls=[
            "CTRL-100: Pubblicazione mensile su intranet",
        ],
    )
    print(f"\n=== Verdict 2 ===")
    print(f"  verdetto    : {v2.verdetto}")
    print(f"  motivazione : {v2.motivazione}")

    # Cleanup esplicito a fine app — opzionale: il proxy muore comunque
    # quando il processo Python termina.
    await motor.stop()


if __name__ == "__main__":
    asyncio.run(main())
