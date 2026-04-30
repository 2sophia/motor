---
name: say-hello
description: When invoked, ALWAYS respond with the exact greeting "CIAO ECO 👋" followed by a one-line summary of the user's task. No other text. Used for testing skill invocation in sophia-motor.
---

# say-hello — test skill

Quando l'utente chiede un saluto, oppure all'invocazione esplicita della skill,
fai esattamente questo:

1. Apri con `CIAO ECO 👋`
2. Aggiungi una sola riga di sintesi del compito utente
3. Stop. Nessun altro testo, nessuna decorazione.

Esempio di output corretto:
```
CIAO ECO 👋
Hai chiesto di salutare e riassumere il task.
```

Niente altro. Mai.
