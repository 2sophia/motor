"""Cleanup helper — rimuove le run dirs sotto `.runs/`.

Edita la chiamata a `clean_runs(...)` per scegliere la policy:

    clean_runs(".runs")                    # rimuove tutto
    clean_runs(".runs", keep_last=5)       # mantiene gli ultimi 5
    clean_runs(".runs", older_than_days=7) # rimuove più vecchi di 7gg
    clean_runs(".runs", dry_run=True)      # mostra cosa rimuoverebbe

Lancia:
    cd /home/mwspace/htdocs/sophia-motor
    .venv/bin/python examples/clean.py
"""
from __future__ import annotations

from sophia_motor import clean_runs


def main() -> None:
    # ── 👇 TODO: scegli la tua policy ───────────────────────────────────
    removed = clean_runs(".runs", keep_last=5, dry_run=False)

    if not removed:
        print("Niente da rimuovere.")
        return

    print(f"Rimossi {len(removed)} run dir:")
    for path in removed:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
