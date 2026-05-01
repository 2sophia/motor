# Copyright (c) 2026 Sophia AI
# SPDX-License-Identifier: MIT
"""Multi-turn chat — the agent remembers prior turns.

Shows three things:
  1. The 4-line API: motor.chat() + chat.send() drives a real dialog.
  2. The multi-user backend pattern (one Motor, dict of Chat per
     conversation) — same as sophia-agent's agent_service.
  3. Persistence — saving (chat_id, session_id) to disk and resuming
     after a process restart.

Run:
    pip install sophia-motor
    ANTHROPIC_API_KEY=sk-ant-... python main.py
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sophia_motor import Motor, MotorConfig


PERSIST = Path("./chat-state.json")


async def example_1_smallest_dialog() -> None:
    """The whole chat API in 4 lines — no helpers, no boilerplate."""
    print("\n" + "═" * 60)
    print("EXAMPLE 1 — smallest possible chat")
    print("═" * 60)

    motor = Motor(MotorConfig(console_log_enabled=False))
    chat = motor.chat()

    r1 = await chat.send("My favorite city is Lisbon. Remember it.")
    print(f"\n  user > My favorite city is Lisbon. Remember it.")
    print(f"  bot  > {r1.output_text[:120]}…")

    r2 = await chat.send("Recommend two restaurants there.")
    print(f"\n  user > Recommend two restaurants there.")
    print(f"  bot  > {r2.output_text[:200]}…")

    print(f"\n  ↳ chat_id={chat.chat_id}  session_id={chat.session_id}")
    await motor.stop()


async def example_2_multi_user_backend() -> None:
    """Same Motor, many users — chat_id keys an in-memory dict.

    This is the FastAPI / Flask / aiohttp pattern. In a real backend
    you'd persist (chat_id, session_id) to your DB instead of an
    in-memory dict, and look it up on every incoming message.
    """
    print("\n" + "═" * 60)
    print("EXAMPLE 2 — multi-user backend pattern")
    print("═" * 60)

    motor = Motor(MotorConfig(console_log_enabled=False))
    chats: dict[str, "object"] = {}    # chat_id -> Chat

    async def handle_message(user_chat_id: str, message: str) -> str:
        """The handler your HTTP endpoint would call."""
        if user_chat_id not in chats:
            chats[user_chat_id] = motor.chat(chat_id=user_chat_id)
        reply = await chats[user_chat_id].send(message)
        return reply.output_text or ""

    # User Alice has a conversation
    a1 = await handle_message("alice", "I'm allergic to peanuts.")
    a2 = await handle_message("alice", "Can I eat a Snickers bar?")
    print(f"\n  alice > I'm allergic to peanuts.\n  bot   > {a1[:80]}…")
    print(f"  alice > Can I eat a Snickers bar?\n  bot   > {a2[:120]}…")

    # User Bob has an INDEPENDENT conversation, same Motor
    b1 = await handle_message("bob", "I prefer to communicate in haiku.")
    b2 = await handle_message("bob", "Tell me about the weather.")
    print(f"\n  bob   > I prefer to communicate in haiku.\n  bot   > {b1[:80]}…")
    print(f"  bob   > Tell me about the weather.\n  bot   > {b2[:120]}…")

    # Each user has their own chat — different cwd, different session
    print(f"\n  ↳ alice.cwd = {chats['alice'].cwd}")
    print(f"  ↳ bob.cwd   = {chats['bob'].cwd}")
    await motor.stop()


async def example_3_persist_and_resume() -> None:
    """Persist (chat_id, session_id) → restart process → resume dialog.

    For the demo we save to a JSON file, but in production it'd be a
    DB row. Two strings is all the state you need to carry.
    """
    print("\n" + "═" * 60)
    print("EXAMPLE 3 — persist + resume across process restart")
    print("═" * 60)

    motor = Motor(MotorConfig(console_log_enabled=False))

    if PERSIST.exists():
        # Resume from saved state
        state = json.loads(PERSIST.read_text())
        chat = motor.chat(
            chat_id=state["chat_id"],
            session_id=state["session_id"],
        )
        print(f"\n  resumed chat {chat.chat_id} (session {chat.session_id})")
        reply = await chat.send("What did I tell you to remember earlier?")
        print(f"  bot  > {reply.output_text[:200]}…")
        # Update saved session_id (it may rotate per-turn on some setups)
        state["session_id"] = chat.session_id
        PERSIST.write_text(json.dumps(state, indent=2))
    else:
        # First run — establish the chat
        chat = motor.chat(chat_id="persisted-demo-001")
        await chat.send("Remember: the secret code is BLUEBERRY.")
        print(f"\n  established chat {chat.chat_id}")
        print(f"  saved state to {PERSIST}")
        PERSIST.write_text(json.dumps({
            "chat_id": chat.chat_id,
            "session_id": chat.session_id,
        }, indent=2))
        print(f"\n  → re-run this script: it'll resume and recall the secret")

    await motor.stop()


async def example_4_reset() -> None:
    """`/new chat` button — same chat_id, fresh memory."""
    print("\n" + "═" * 60)
    print("EXAMPLE 4 — chat.reset() (new SDK session, same workspace)")
    print("═" * 60)

    motor = Motor(MotorConfig(console_log_enabled=False))
    chat = motor.chat()

    await chat.send("My password is hunter2.")
    r1 = await chat.send("What's my password?")
    print(f"\n  before reset — bot recalls: {r1.output_text[:80]}…")

    await chat.reset()
    r2 = await chat.send("What's my password?")
    print(f"\n  after  reset — bot recalls: {r2.output_text[:120]}…")
    print(f"  (same chat_id={chat.chat_id}, fresh session_id={chat.session_id})")

    await motor.stop()


async def main() -> None:
    await example_1_smallest_dialog()
    await example_2_multi_user_backend()
    await example_3_persist_and_resume()
    await example_4_reset()


if __name__ == "__main__":
    asyncio.run(main())
