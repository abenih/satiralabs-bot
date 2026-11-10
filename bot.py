#!/usr/bin/env python3
"""
SatiraLabs inquiry bot.

A single-file Telegram bot built only on the Python standard library.
It talks to the Telegram Bot API over HTTPS with urllib, uses long
polling (getUpdates) so it needs no webhook, domain or public port,
and stores inquiries in a local SQLite database.

Configuration comes from environment variables (see .env.example):
    BOT_TOKEN      - token issued by @BotFather
    OWNER_CHAT_ID  - numeric Telegram chat id of the owner (inquiries are
                     forwarded there, and only that chat may use /inquiries)

Run it with:  python3 bot.py
"""

import json
import logging
import os
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path

# --------------------------------------------------------------------------
# Constants: copy mirrors the SatiraLabs website (https://satiralabs.com)
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BOT_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "inquiries.db"

SITE = "https://satiralabs.com"
CONTACT_EMAIL = "hello@satiralabs.com"
GITHUB = "https://github.com/abenih"

# Welcome picture sent at the top of every /start greeting. Resolved
# relative to this file, so it works both in the repo and when deployed
# to /opt/satiralabs-bot/ (assets/welcome.jpg sits next to bot.py).
WELCOME_PHOTO_PATH = Path(os.environ.get(
    "WELCOME_PHOTO_PATH", BASE_DIR / "assets" / "welcome.jpg"))
WELCOME_CAPTION = "Welcome to SatiraLabs 👋"

SERVICES = [
    ("🌐 Websites", "Fast, mobile-first websites built to convert — booking, ordering and lead capture wired in from day one."),
    ("🤖 Apps & Telegram Bots", "Telegram bots and Mini Apps your customers open in one tap — catalogues, ordering, payments, bookings and support."),
    ("🔍 SEO", "Technical fixes, keyword strategy and content that put you on page one for the searches that bring buyers."),
    ("✨ AI Chatbots & Automation", "AI assistants that answer prices, qualify leads, book appointments and follow up automatically — 24/7."),
    ("📣 Digital Ads & Boosting", "Meta, Google, TikTok and Telegram campaigns with landing pages built to convert the click."),
    ("🛡️ Security Audits", "External security assessments with client-ready reports — email authentication, DNS, hosting and CMS hardening."),
]

# (slug, display name, one-liner) — slugs match the live site structure.
PORTFOLIO = [
    ("hotel-booking", "Azure Bay Hotel", "hotel website with direct-booking flow"),
    ("restaurant", "Ember & Oak", "fine-dining site with menu and reservations"),
    ("ecommerce", "Nordwind Store", "fashion storefront with cart, wishlist and search"),
    ("real-estate", "Havenmark Realty", "property listings with enquiry capture"),
    ("clinic", "ClearPath Clinic", "clinic site with appointment booking"),
    ("dashboard", "GrowthBoard", "SEO & ads analytics dashboard"),
    ("fitness", "PulseFit Studio", "fitness studio with schedules and membership signup"),
    ("tours", "Savanna Trails Tours", "tour operator with packages and booking enquiry"),
    ("coffee", "Buna House Coffee", "specialty coffee shop with menu and ordering info"),
]

BUDGETS = ["Under $500", "$500 – $1,500", "$1,500 – $5,000", "Over $5,000", "Not sure yet"]

FAQ_ITEMS = [
    ("💰 How much does a project cost?",
     "Every project is quoted individually after a free 15-minute call — the price depends on what "
     "we found in the research and what needs building. There are no package prices; tap "
     "“💬 Send an inquiry” and we will come back with a specific quote."),
    ("⏱ How long does it take?",
     "A focused landing page or bot can ship in days; a full website or automation system usually "
     "takes 1–4 weeks. You see a working version in your branding early — no big reveal at the end."),
    ("🚀 How do we start?",
     "Send an inquiry here (or email " + CONTACT_EMAIL + "). We start with research into your "
     "business, customers and competitors, then build → launch → grow, measuring results from day one. "
     "You can also explore our work on GitHub: " + GITHUB + "."),
    ("🗣 Which languages do you work in?",
     "English and Amharic. We work with clients worldwide, entirely online."),
    ("🌍 Where are you located?",
     "We work with clients worldwide, entirely online. Replies land within one business day, "
     "Monday to Saturday."),
]

# Main reply keyboard buttons.
BTN_SERVICES = "📋 Our services"
BTN_PORTFOLIO = "🖼 Portfolio"
BTN_INQUIRY = "💬 Send an inquiry"
BTN_FAQ = "❓ FAQ"
BTN_CONTACT = "✉️ Contact"
BTN_SKIP = "⏭ Skip"
BTN_CANCEL = "❌ Cancel"
BTN_MAIN_MENU = "🏠 Main menu"
BTN_USE_TELEGRAM = "👤 Use my Telegram"

MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": BTN_INQUIRY}],
        [{"text": BTN_SERVICES}, {"text": BTN_PORTFOLIO}],
        [{"text": BTN_FAQ}, {"text": BTN_CONTACT}],
    ],
    "resize_keyboard": True,
}

# Section screens add a Main menu row under the standard rows.
SECTION_KEYBOARD = {
    "keyboard": MAIN_KEYBOARD["keyboard"] + [[{"text": BTN_MAIN_MENU}]],
    "resize_keyboard": True,
}

# Old slash commands that used to exist; typed copies get menu guidance,
# never a handler (navigation is buttons-only).
RETIRED_COMMANDS = {"/services", "/portfolio", "/inquiry", "/faq", "/contact"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("satiralabs-bot")


# --------------------------------------------------------------------------
# Minimal .env loader (KEY=VALUE lines; real environment wins)
# --------------------------------------------------------------------------

def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------
# Telegram Bot API layer (transport is swappable for tests)
# --------------------------------------------------------------------------

class TelegramAPI:
    def __init__(self, token: str, opener=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        # opener(method_name, params_dict) -> response dict; default = real HTTPS.
        self._opener = opener or self._https_opener
        self._custom_opener = opener is not None

    def _https_opener(self, method: str, params: dict) -> dict:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            self.base + method,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=70) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _https_upload(self, method: str, chat_id, photo_path: Path,
                      caption=None) -> dict:
        """Real sendPhoto: multipart/form-data upload built with stdlib only."""
        boundary = "satiralabs-" + uuid.uuid4().hex
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        body = bytearray()
        for name, value in fields.items():
            body += (f"--{boundary}\r\n"
                     f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n").encode("utf-8")
        suffix = photo_path.suffix.lower()
        content_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".png": "image/png", ".webp": "image/webp"}.get(
                            suffix, "application/octet-stream")
        body += (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="photo"; '
                 f'filename="{photo_path.name}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n").encode("utf-8")
        body += photo_path.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            self.base + method,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=70) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def call(self, method: str, **params) -> dict:
        try:
            result = self._opener(method, params)
        except urllib.error.URLError as exc:
            log.warning("API call %s failed: %s", method, exc)
            return {}
        if not result.get("ok", True):
            log.warning("API call %s returned error: %s", method, result)
        return result

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        params = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode:
            params["parse_mode"] = parse_mode
        return self.call("sendMessage", **params)

    def send_photo(self, chat_id, photo_path, caption=None):
        """Upload a local image file via sendPhoto.

        With the real transport this is a multipart/form-data upload;
        with an injected opener (tests) the call is recorded like any
        other. Failures are logged and swallowed — a photo must never
        break the conversation flow.
        """
        params = {"chat_id": chat_id, "photo": str(photo_path)}
        if caption:
            params["caption"] = caption
        if self._custom_opener:
            return self.call("sendPhoto", **params)
        try:
            result = self._https_upload("sendPhoto", chat_id,
                                        Path(photo_path), caption)
        except Exception as exc:  # URLError, OSError (missing file), …
            log.warning("sendPhoto upload failed: %s", exc)
            return {}
        if not result.get("ok", True):
            log.warning("sendPhoto returned error: %s", result)
        return result

    def answer_callback(self, callback_query_id, text=None):
        params = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        return self.call("answerCallbackQuery", **params)

    def get_updates(self, offset, timeout=50):
        return self.call("getUpdates", offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])


# --------------------------------------------------------------------------
# Inquiry storage (SQLite)
# --------------------------------------------------------------------------

class InquiryStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inquiries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                chat_id     INTEGER NOT NULL,
                name        TEXT NOT NULL,
                business    TEXT,
                service     TEXT NOT NULL,
                budget      TEXT,
                message     TEXT NOT NULL,
                contact     TEXT NOT NULL,
                tg_username TEXT
            )
            """
        )
        self.conn.commit()

    def save(self, data: dict) -> str:
        """Persist one inquiry dict; returns its reference number, e.g. SL-0007."""
        cur = self.conn.execute(
            """INSERT INTO inquiries
               (chat_id, name, business, service, budget, message, contact, tg_username)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["chat_id"], data["name"], data.get("business"), data["service"],
             data.get("budget"), data["message"], data["contact"], data.get("tg_username")),
        )
        self.conn.commit()
        return f"SL-{cur.lastrowid:04d}"

    def latest(self, limit: int = 10) -> list:
        rows = self.conn.execute(
            """SELECT id, created_at, name, business, service, budget, contact
               FROM inquiries ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"ref": f"SL-{r[0]:04d}", "created_at": r[1], "name": r[2],
             "business": r[3], "service": r[4], "budget": r[5], "contact": r[6]}
            for r in rows
        ]


# --------------------------------------------------------------------------
# Conversation logic — a small per-chat state machine for the inquiry flow.
# The Bot class is transport-agnostic: handle_update() drives everything,
# which is what the offline tests exercise.
# --------------------------------------------------------------------------

# FSM steps
STEP_NAME, STEP_BUSINESS, STEP_SERVICE, STEP_BUDGET, STEP_MESSAGE, STEP_CONTACT, STEP_CONFIRM = range(7)

STEP_PROMPTS = {
    STEP_NAME: "Great — let's put your inquiry together.\n\n1️⃣ What is your *name*?",
    STEP_BUSINESS: "2️⃣ What is your *business name*? (optional — tap Skip if you prefer)",
    STEP_SERVICE: "3️⃣ Which *service* do you need? Pick one below.",
    STEP_BUDGET: "4️⃣ Do you have a *budget range* in mind? (optional)",
    STEP_MESSAGE: "5️⃣ Tell us about the project — what do you want to build or fix?",
    STEP_CONTACT: "6️⃣ How should we *reply* to you? Tap the button to use your Telegram, or type an email address or phone number.",
}


def _keyboard(rows):
    return {"keyboard": [[{"text": t} for t in row] for row in rows],
            "resize_keyboard": True, "one_time_keyboard": True}


SERVICE_KEYBOARD = _keyboard([[s[0]] for s in SERVICES] + [["Other"], [BTN_CANCEL]])
BUDGET_KEYBOARD = _keyboard([[b] for b in BUDGETS] + [[BTN_SKIP], [BTN_CANCEL]])
# Bare flow keyboard: just a Cancel affordance, used on free-text steps.
FLOW_KEYBOARD = _keyboard([[BTN_CANCEL]])


class Bot:
    def __init__(self, api: TelegramAPI, store: InquiryStore, owner_chat_id=None):
        self.api = api
        self.store = store
        self.owner_chat_id = owner_chat_id
        self.sessions = {}  # chat_id -> {"step": int, "data": dict}

    # -- helpers ----------------------------------------------------------

    def _is_owner(self, chat_id) -> bool:
        return self.owner_chat_id is not None and str(chat_id) == str(self.owner_chat_id)

    def _send(self, chat_id, text, markup=MAIN_KEYBOARD):
        self.api.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    def _send_welcome_photo(self, chat_id):
        """Best-effort welcome picture for /start.

        A missing file (or any upload failure) is logged and the caller
        falls back to the plain text greeting — /start must never crash
        over a picture.
        """
        if not WELCOME_PHOTO_PATH.exists():
            log.warning("Welcome photo not found at %s — text-only greeting.",
                        WELCOME_PHOTO_PATH)
            return
        try:
            self.api.send_photo(chat_id, WELCOME_PHOTO_PATH,
                                caption=WELCOME_CAPTION)
        except Exception:
            log.exception("Welcome photo send failed — text-only greeting.")

    # -- entry point ------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not message:
            return  # edits, joins, channel events: nothing to do
        chat_id = message["chat"]["id"]
        user = message.get("from", {})

        text = message.get("text")
        if text is None:
            # Photos, stickers, voice notes… ask for text instead of crashing.
            self._send(chat_id, "I can work with text messages — please type your reply, "
                                "or tap one of the menu buttons below.")
            return

        text = text.strip()
        if text == "/cancel" or text == BTN_CANCEL:
            self.sessions.pop(chat_id, None)
            self._send(chat_id, "No problem — cancelled. Anything else I can help with?")
            return
        if text.startswith("/start"):
            self.sessions.pop(chat_id, None)
            first = user.get("first_name", "there")
            self._send_welcome_photo(chat_id)
            self._send(chat_id,
                       f"👋 Hello {first}, welcome to *SatiraLabs*!\n\n"
                       "We're a founder-led digital studio working worldwide, building websites, "
                       "apps & Telegram bots, SEO, AI automation, ads and security audits for "
                       "businesses worldwide.\n\n"
                       "Ask me about our services or portfolio — or tap *💬 Send an inquiry* "
                       "and we'll reply within one business day.")
            return
        if text.startswith("/inquiries"):
            # Owner-only and never advertised: everyone else just gets
            # the standard menu guidance, as if it did not exist.
            if self._is_owner(chat_id):
                self._cmd_inquiries(chat_id)
            else:
                self._menu_guidance(chat_id)
            return
        if text.split(" ")[0] in RETIRED_COMMANDS:
            self._menu_guidance(chat_id)
            return

        # If the visitor is mid-inquiry, the FSM consumes the message.
        if chat_id in self.sessions:
            self._fsm_text(chat_id, user, text)
            return

        # Top-level menu routing (buttons only).
        if text == BTN_SERVICES:
            self._show_services(chat_id)
        elif text == BTN_PORTFOLIO:
            self._show_portfolio(chat_id)
        elif text == BTN_INQUIRY:
            self._start_inquiry(chat_id)
        elif text == BTN_FAQ:
            self._show_faq(chat_id)
        elif text == BTN_MAIN_MENU:
            self._send(chat_id, "🏠 *Main menu* — pick an option below, or tap "
                                "*💬 Send an inquiry* to tell us what you need.")
        elif text == BTN_CONTACT:
            self._send(chat_id,
                       "✉️ *Contact SatiraLabs*\n\n"
                       f"Email: {CONTACT_EMAIL}\n"
                       f"Website: {SITE}\n"
                       f"GitHub: {GITHUB}\n\n"
                       "Or tap *💬 Send an inquiry* right here — replies within one business day, "
                       "Monday to Saturday.",
                       markup=SECTION_KEYBOARD)
        else:
            self._menu_guidance(chat_id)

    def _menu_guidance(self, chat_id):
        self._send(chat_id, "Everything here works with the buttons below — tap "
                            "*💬 Send an inquiry* to tell us what you need, or browse "
                            "services and portfolio.")

    # -- static content ---------------------------------------------------

    def _show_services(self, chat_id):
        lines = ["📋 *Our services*\n"]
        for title, desc in SERVICES:
            lines.append(f"{title}\n{desc}\n")
        lines.append("How we work: research → build → launch → grow. Every engagement starts "
                     "with a free 15-minute call, and replies land within one business day.")
        self._send(chat_id, "\n".join(lines), markup=SECTION_KEYBOARD)

    def _show_portfolio(self, chat_id):
        lines = ["🖼 *Selected work* — live projects you can explore:\n"]
        for slug, name, blurb in PORTFOLIO:
            lines.append(f"• *{name}* — {blurb}\n  {SITE}/portfolio/{slug}/")
        lines.append("\nYours would be designed around your business, your content and your customers.")
        self._send(chat_id, "\n".join(lines), markup=SECTION_KEYBOARD)

    def _show_faq(self, chat_id):
        lines = ["❓ *Frequently asked questions*\n"]
        for question, answer in FAQ_ITEMS:
            lines.append(f"{question}\n{answer}\n")
        self._send(chat_id, "\n".join(lines), markup=SECTION_KEYBOARD)

    # -- owner command ----------------------------------------------------

    def _cmd_inquiries(self, chat_id):
        # Owner-only; handle_update routes non-owners to menu guidance.
        if not self._is_owner(chat_id):
            self._menu_guidance(chat_id)
            return
        items = self.store.latest(10)
        if not items:
            self._send(chat_id, "📭 No inquiries stored yet.")
            return
        lines = ["🗂 *Latest inquiries*\n"]
        for it in items:
            biz = f" ({it['business']})" if it["business"] else ""
            budget = f" · {it['budget']}" if it["budget"] else ""
            lines.append(f"*{it['ref']}* — {it['name']}{biz}\n"
                         f"{it['service']}{budget}\n"
                         f"Contact: {it['contact']} · {it['created_at']} UTC\n")
        self._send(chat_id, "\n".join(lines))

    # -- inquiry FSM --------------------------------------------------------

    def _start_inquiry(self, chat_id):
        self.sessions[chat_id] = {"step": STEP_NAME, "data": {}}
        self._send(chat_id, STEP_PROMPTS[STEP_NAME] + "\n\nTap *❌ Cancel* anytime to stop.",
                   markup=FLOW_KEYBOARD)

    def _fsm_text(self, chat_id, user, text):
        session = self.sessions[chat_id]
        step, data = session["step"], session["data"]

        if step == STEP_NAME:
            if len(text) < 2:
                self._send(chat_id, "Please tell me your name so we know who we're talking to.",
                           markup=FLOW_KEYBOARD)
                return
            data["name"] = text
            session["step"] = STEP_BUSINESS
            self._send(chat_id, STEP_PROMPTS[STEP_BUSINESS],
                       markup=_keyboard([[BTN_SKIP], [BTN_CANCEL]]))

        elif step == STEP_BUSINESS:
            data["business"] = None if text == BTN_SKIP else text
            session["step"] = STEP_SERVICE
            self._send(chat_id, STEP_PROMPTS[STEP_SERVICE], markup=SERVICE_KEYBOARD)

        elif step == STEP_SERVICE:
            titles = [s[0] for s in SERVICES] + ["Other"]
            if text not in titles:
                self._send(chat_id, "Please pick one of the services from the keyboard — "
                                    "or choose *Other*.", markup=SERVICE_KEYBOARD)
                return
            data["service"] = text
            session["step"] = STEP_BUDGET
            self._send(chat_id, STEP_PROMPTS[STEP_BUDGET], markup=BUDGET_KEYBOARD)

        elif step == STEP_BUDGET:
            data["budget"] = None if text == BTN_SKIP else text
            session["step"] = STEP_MESSAGE
            self._send(chat_id, STEP_PROMPTS[STEP_MESSAGE], markup=FLOW_KEYBOARD)

        elif step == STEP_MESSAGE:
            if len(text) < 5:
                self._send(chat_id, "Give us a sentence or two about the project so we can "
                                    "come back with something useful.",
                           markup=FLOW_KEYBOARD)
                return
            data["message"] = text
            session["step"] = STEP_CONTACT
            username = user.get("username")
            rows = [[BTN_USE_TELEGRAM], [BTN_CANCEL]] if username else [[BTN_CANCEL]]
            self._send(chat_id, STEP_PROMPTS[STEP_CONTACT], markup=_keyboard(rows))

        elif step == STEP_CONTACT:
            username = user.get("username")
            if text == BTN_USE_TELEGRAM and username:
                data["contact"] = f"Telegram @{username}"
            else:
                data["contact"] = text
            data["tg_username"] = f"@{username}" if username else None
            data["chat_id"] = chat_id
            session["step"] = STEP_CONFIRM
            self._send_summary(chat_id, data)

        elif step == STEP_CONFIRM:
            # Visitor typed instead of tapping a button.
            self._send(chat_id, "Please tap *Confirm*, *Edit* or *Cancel* above — "
                                "or tap *❌ Cancel* to stop.")

    def _send_summary(self, chat_id, data):
        summary = (
            "📝 *Your inquiry — please check it*\n\n"
            f"Name: {data['name']}\n"
            f"Business: {data.get('business') or '—'}\n"
            f"Service: {data['service']}\n"
            f"Budget: {data.get('budget') or '—'}\n"
            f"Message: {data['message']}\n"
            f"Reply to: {data['contact']}\n\n"
            "Tap Confirm to send it to the SatiraLabs team."
        )
        markup = {"inline_keyboard": [[
            {"text": "✅ Confirm", "callback_data": "inq:confirm"},
            {"text": "✏️ Edit", "callback_data": "inq:edit"},
            {"text": "❌ Cancel", "callback_data": "inq:cancel"},
        ]]}
        self._send(chat_id, summary, markup=markup)

    def _handle_callback(self, query):
        self.api.answer_callback(query["id"])
        chat_id = query["message"]["chat"]["id"]
        action = query.get("data", "")
        session = self.sessions.get(chat_id)
        if session is None or session["step"] != STEP_CONFIRM:
            return
        data = session["data"]

        if action == "inq:confirm":
            ref = self.store.save(data)
            self._forward_to_owner(ref, data)
            self.sessions.pop(chat_id, None)
            self._send(chat_id,
                       "✅ Thank you for reaching out to SatiraLabs!\n\n"
                       f"Your inquiry has been received — your reference number is {ref}.\n"
                       "We'll get back to you within one business day.\n\n"
                       "Thank you for considering us 🙏")
        elif action == "inq:edit":
            # Start the form over, keeping nothing half-edited.
            self.sessions[chat_id] = {"step": STEP_NAME, "data": {}}
            self._send(chat_id, "OK — let's redo it.\n\n" + STEP_PROMPTS[STEP_NAME],
                       markup=FLOW_KEYBOARD)
        elif action == "inq:cancel":
            self.sessions.pop(chat_id, None)
            self._send(chat_id, "Inquiry discarded. Anything else I can help with?")

    def _forward_to_owner(self, ref, data):
        if not self.owner_chat_id:
            log.info("OWNER_CHAT_ID not set — inquiry %s stored locally only; "
                     "forwarding pending owner setup.", ref)
            return
        text = (
            f"🔔 *New inquiry {ref}*\n\n"
            f"Name: {data['name']}\n"
            f"Business: {data.get('business') or '—'}\n"
            f"Service: {data['service']}\n"
            f"Budget: {data.get('budget') or '—'}\n"
            f"Message: {data['message']}\n"
            f"Reply to: {data['contact']}\n"
            f"Telegram chat: {data['chat_id']}"
        )
        self.api.send_message(self.owner_chat_id, text, parse_mode="Markdown")


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main() -> None:
    load_env_file(BASE_DIR / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is not set — see SETUP.md / .env.example.")
    owner = os.environ.get("OWNER_CHAT_ID", "").strip() or None

    api = TelegramAPI(token)
    store = InquiryStore(DB_PATH)
    bot = Bot(api, store, owner_chat_id=owner)

    log.info("SatiraLabs bot starting (long polling). Owner forwarding: %s",
             "on" if owner else "off (OWNER_CHAT_ID unset)")
    offset = 0
    while True:
        try:
            result = api.get_updates(offset=offset, timeout=50)
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                try:
                    bot.handle_update(update)
                except Exception:  # one bad update must not kill the bot
                    log.exception("Failed to handle update %s", update.get("update_id"))
        except Exception:
            log.exception("Polling error; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
