#!/usr/bin/env python3
"""
Offline dry-run tests for the SatiraLabs bot.

The Telegram transport is stubbed out, so the full inquiry state machine
is exercised end to end — with no token and no network:

    python3 test_fsm.py
"""

import tempfile
from pathlib import Path

from bot import Bot, TelegramAPI, InquiryStore
import bot as botmod


class StubTransport:
    """Records API calls instead of performing HTTPS requests."""

    def __init__(self):
        self.calls = []  # list of (method, params)

    def __call__(self, method, params):
        self.calls.append((method, params))
        return {"ok": True, "result": {}}

    def messages_to(self, chat_id):
        return [p["text"] for m, p in self.calls
                if m == "sendMessage" and p["chat_id"] == chat_id]

    def photos_to(self, chat_id):
        return [p for m, p in self.calls
                if m == "sendPhoto" and p["chat_id"] == chat_id]


def msg(chat_id, text, username="testuser", first_name="Test"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "first_name": first_name, "username": username},
            "text": text,
        },
    }


def callback(chat_id, data):
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": chat_id, "first_name": "Test", "username": "testuser"},
            "message": {"message_id": 9, "chat": {"id": chat_id, "type": "private"}},
            "data": data,
        },
    }


def run() -> None:
    transport = StubTransport()
    api = TelegramAPI("TEST-TOKEN-NOT-REAL", opener=transport)
    store = InquiryStore(Path(tempfile.mkdtemp()) / "test.db")
    bot = Bot(api, store, owner_chat_id=999)

    visitor = 12345

    # /start sends the welcome photo FIRST, then greets and mentions SatiraLabs.
    bot.handle_update(msg(visitor, "/start"))
    assert any("SatiraLabs" in t for t in transport.messages_to(visitor)), "start greeting"
    photos = transport.photos_to(visitor)
    assert len(photos) == 1, "welcome photo sent on /start"
    assert photos[0].get("caption") == botmod.WELCOME_CAPTION, "welcome caption"
    assert str(photos[0].get("photo", "")).endswith("welcome.jpg"), "welcome file"
    first_photo_idx = next(i for i, (m, _) in enumerate(transport.calls)
                           if m == "sendPhoto")
    first_msg_idx = next(i for i, (m, p) in enumerate(transport.calls)
                         if m == "sendMessage" and p["chat_id"] == visitor)
    assert first_photo_idx < first_msg_idx, "photo precedes greeting text"

    # Missing welcome file: /start must fall back to text-only, never crash.
    real_path = botmod.WELCOME_PHOTO_PATH
    botmod.WELCOME_PHOTO_PATH = real_path.parent / "does-not-exist.jpg"
    try:
        photo_count = len(transport.photos_to(visitor))
        bot.handle_update(msg(visitor, "/start"))
        assert len(transport.photos_to(visitor)) == photo_count, "no photo when file missing"
        assert any("SatiraLabs" in t for t in transport.messages_to(visitor)), \
            "text greeting still sent without photo"
    finally:
        botmod.WELCOME_PHOTO_PATH = real_path

    # Menu sections respond.
    for button in (botmod.BTN_SERVICES, botmod.BTN_PORTFOLIO, botmod.BTN_FAQ, botmod.BTN_CONTACT):
        bot.handle_update(msg(visitor, button))
    texts = transport.messages_to(visitor)
    assert any("satiralabs.com/portfolio/hotel-booking/" in t for t in texts), "portfolio links"
    assert any(botmod.CONTACT_EMAIL in t for t in texts), "contact email"

    # Full inquiry walk-through.
    bot.handle_update(msg(visitor, botmod.BTN_INQUIRY))
    bot.handle_update(msg(visitor, "Abebe Kebede"))                    # name
    bot.handle_update(msg(visitor, botmod.BTN_SKIP))                    # business (skip)
    bot.handle_update(msg(visitor, botmod.SERVICES[0][0]))              # service: Websites
    bot.handle_update(msg(visitor, botmod.BUDGETS[1]))                  # budget
    bot.handle_update(msg(visitor, "We need a booking site for our hotel."))
    bot.handle_update(msg(visitor, botmod.BTN_USE_TELEGRAM))            # contact via @username
    assert any("Your inquiry" in t for t in transport.messages_to(visitor)), "summary shown"

    # Confirm via inline button.
    bot.handle_update(callback(visitor, "inq:confirm"))
    confirmations = [t for t in transport.messages_to(visitor) if "reference number" in t]
    assert confirmations and "SL-0001" in confirmations[0], "visitor confirmation + ref"

    saved = store.latest(5)
    assert len(saved) == 1 and saved[0]["name"] == "Abebe Kebede", "inquiry stored"
    assert any("New inquiry SL-0001" in t for t in transport.messages_to(999)), "owner forwarded"

    # Owner can list inquiries; a visitor gets plain menu guidance —
    # the command is hidden and never acknowledged to visitors.
    bot.handle_update(msg(999, "/inquiries", username="owner"))
    assert any("SL-0001" in t for t in transport.messages_to(999)), "owner /inquiries"
    before = len(transport.messages_to(visitor))
    bot.handle_update(msg(visitor, "/inquiries"))
    assert any("buttons below" in t for t in transport.messages_to(visitor)[before:]), \
        "visitor typing /inquiries gets menu guidance"

    # Retired commands fall through to menu guidance, no handler fires.
    for cmd in ("/services", "/portfolio", "/inquiry", "/faq", "/contact"):
        before = len(transport.messages_to(visitor))
        bot.handle_update(msg(visitor, cmd))
        new = transport.messages_to(visitor)[before:]
        assert any("buttons below" in t for t in new), f"{cmd} yields menu guidance"

    # Main menu button returns the menu state.
    before = len(transport.messages_to(visitor))
    bot.handle_update(msg(visitor, botmod.BTN_MAIN_MENU))
    assert any("Main menu" in t for t in transport.messages_to(visitor)[before:]), \
        "main menu button responds"

    # /cancel aborts a fresh flow.
    bot.handle_update(msg(visitor, botmod.BTN_INQUIRY))
    assert visitor in bot.sessions
    bot.handle_update(msg(visitor, "/cancel"))
    assert visitor not in bot.sessions, "cancel clears session"

    # The on-screen Cancel button aborts a flow too.
    bot.handle_update(msg(visitor, botmod.BTN_INQUIRY))
    assert visitor in bot.sessions
    bot.handle_update(msg(visitor, botmod.BTN_CANCEL))
    assert visitor not in bot.sessions, "cancel button clears session"

    # Non-text noise is handled gracefully.
    bot.handle_update({"update_id": 3, "message": {
        "message_id": 5, "chat": {"id": visitor, "type": "private"},
        "from": {"id": visitor, "first_name": "Test"},
        "sticker": {"file_id": "x"}}})
    assert any("text messages" in t for t in transport.messages_to(visitor)), "noise handled"

    print("All offline FSM tests passed.")


if __name__ == "__main__":
    run()
