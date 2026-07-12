from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.database import ListingStatus, STATUS_LABELS, init_db
from app.services import extract_url, is_divar_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = settings.dashboard_url.rstrip("/")

# action name -> ListingStatus, or special: delete / note
STATUS_ACTIONS: dict[str, ListingStatus | str] = {
    "call": ListingStatus.NEED_CALL,
    "needcall": ListingStatus.NEED_CALL,
    "need_call": ListingStatus.NEED_CALL,
    "reject": ListingStatus.REJECTED,
    "rejected": ListingStatus.REJECTED,
    "accept": ListingStatus.IN_TALK,
    "talk": ListingStatus.IN_TALK,
    "in_talk": ListingStatus.IN_TALK,
    "bought": ListingStatus.BOUGHT,
    "buy": ListingStatus.BOUGHT,
    "done": ListingStatus.BOUGHT,
    "wait": ListingStatus.WAITLIST,
    "waitlist": ListingStatus.WAITLIST,
    "noanswer": ListingStatus.NO_ANSWER,
    "no_answer": ListingStatus.NO_ANSWER,
    "new": ListingStatus.NEW,
}

SPECIAL_ACTIONS = {"remove", "delete", "del", "note"}

STATUS_ALIASES = {k.replace("_", ""): v for k, v in STATUS_ACTIONS.items() if isinstance(v, ListingStatus)}
for k, v in list(STATUS_ACTIONS.items()):
    if isinstance(v, ListingStatus):
        STATUS_ALIASES[k] = v

TEXT_ACTION_RE = re.compile(
    r"^(?P<action>call|needcall|need_call|reject|rejected|accept|talk|bought|buy|done|wait|waitlist|noanswer|no_answer|new|remove|delete|del|note)\s+(?P<target>.+)$",
    re.IGNORECASE,
)


def allowed(user_id: int) -> bool:
    allowed_ids = settings.allowed_user_ids
    return not allowed_ids or user_id in allowed_ids


async def guard(update: Update) -> bool:
    if not update.effective_user or not allowed(update.effective_user.id):
        if update.message:
            await update.message.reply_text("You are not allowed to use this bot.")
        elif update.callback_query:
            await update.callback_query.answer("Not allowed", show_alert=True)
        return False
    return True


# ── API client ──────────────────────────────────────────────

async def api_request(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.request(method, f"{API_BASE}{path}", **kwargs)


async def api_post_listing(url: str, chat_id: str) -> dict:
    response = await api_request("POST", "/api/listings", json={"url": url, "source_chat_id": chat_id})
    if response.status_code == 409:
        return {"duplicate": True, **response.json()}
    response.raise_for_status()
    return response.json()


async def api_lookup(query: str) -> dict:
    response = await api_request("GET", "/api/listings/lookup", params={"q": query})
    response.raise_for_status()
    return response.json()


async def api_move(listing_id: int, status: ListingStatus) -> dict:
    response = await api_request(
        "POST", f"/api/listings/{listing_id}/move", json={"status": status.value}
    )
    response.raise_for_status()
    return response.json()


async def api_patch(listing_id: int, **fields) -> dict:
    response = await api_request("PATCH", f"/api/listings/{listing_id}", json=fields)
    response.raise_for_status()
    return response.json()


async def api_delete(listing_id: int) -> None:
    response = await api_request("DELETE", f"/api/listings/{listing_id}")
    response.raise_for_status()


async def api_list(status: Optional[str] = None, limit: int = 8) -> list[dict]:
    params = {"limit": limit}
    if status:
        params["status"] = status
    response = await api_request("GET", "/api/listings", params=params)
    response.raise_for_status()
    return response.json()


# ── Formatting ──────────────────────────────────────────────

def _format_specs_line(specs: dict) -> str:
    keys = ["متراژ", "اتاق", "طبقه", "ساخت", "قیمت کل", "قیمت هر متر"]
    parts = []
    for key in keys:
        if key in specs:
            parts.append(f"{key}: {specs[key]}")
    return "\n".join(parts[:6])


def _format_caption(data: dict, header: str = "ذخیره شد در برد") -> str:
    lines = [header, "", data.get("title", "آگهی")]
    if data.get("location"):
        lines.append(data["location"])
    if data.get("price"):
        lines.append(data["price"])
    spec_line = _format_specs_line(data.get("specs") or {})
    if spec_line:
        lines.extend(["", spec_line])
    status = data.get("status_label") or STATUS_LABELS.get(
        ListingStatus(data["status"]), data.get("status", "—")
    ) if data.get("status") else "—"
    lines.extend(["", f"وضعیت: {status}", f"شناسه: {data.get('id', '—')}"])
    if data.get("url"):
        lines.extend(["", data["url"]])
    caption = "\n".join(lines)
    return caption[:1020] + ("..." if len(caption) > 1024 else "")


def status_keyboard(listing_id: int) -> InlineKeyboardMarkup:
    buttons = [
        (ListingStatus.NEED_CALL, f"📞 {STATUS_LABELS[ListingStatus.NEED_CALL]}"),
        (ListingStatus.NO_ANSWER, f"🔇 {STATUS_LABELS[ListingStatus.NO_ANSWER]}"),
        (ListingStatus.IN_TALK, f"✅ {STATUS_LABELS[ListingStatus.IN_TALK]}"),
        (ListingStatus.WAITLIST, f"⏳ {STATUS_LABELS[ListingStatus.WAITLIST]}"),
        (ListingStatus.REJECTED, f"❌ {STATUS_LABELS[ListingStatus.REJECTED]}"),
        (ListingStatus.BOUGHT, f"🏠 {STATUS_LABELS[ListingStatus.BOUGHT]}"),
    ]
    rows, row = [], []
    for status, label in buttons:
        row.append(InlineKeyboardButton(label, callback_data=f"move:{listing_id}:{status.value}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🗑 حذف", callback_data=f"delete:{listing_id}:ask"),
        InlineKeyboardButton("📋 برد", url=f"{API_BASE}/"),
    ])
    return InlineKeyboardMarkup(rows)


async def send_listing_message(update: Update, data: dict, header: str = "ذخیره شد در برد") -> None:
    caption = _format_caption(data, header)
    keyboard = status_keyboard(data["id"])
    image_url = data.get("image_url")

    if update.message:
        sender = update.message
    elif update.callback_query and update.callback_query.message:
        sender = update.callback_query.message
        await update.callback_query.answer()
    else:
        return

    if image_url:
        await sender.reply_photo(photo=image_url, caption=caption, reply_markup=keyboard)
    else:
        await sender.reply_text(caption, reply_markup=keyboard, disable_web_page_preview=False)


# ── Core actions ────────────────────────────────────────────

async def resolve_target(target: str) -> dict:
    target = target.strip()
    url = extract_url(target)
    try:
        return await api_lookup(url or target)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ValueError(f"Listing not found: {target}") from exc
        raise


async def apply_status(update: Update, data: dict, status: ListingStatus, header: str) -> None:
    updated = await api_move(data["id"], status)
    await send_listing_message(update, updated, header=f"{header} → {STATUS_LABELS[status]}")


async def apply_delete(update: Update, listing_id: int) -> None:
    await api_delete(listing_id)
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text(f"آگهی #{listing_id} از برد حذف شد.")


async def apply_note(update: Update, target: str, note_text: str) -> None:
    data = await resolve_target(target)
    existing = (data.get("notes") or "").strip()
    merged = f"{existing}\n{note_text}".strip() if existing else note_text
    updated = await api_patch(data["id"], notes=merged)
    await send_listing_message(update, updated, header="یادداشت ذخیره شد")


async def handle_action_on_target(
    update: Update, action: str, target: str, note_text: Optional[str] = None
) -> None:
    action = action.lower().replace("-", "_")

    if action in {"remove", "delete", "del"}:
        data = await resolve_target(target)
        await apply_delete(update, data["id"])
        return

    if action == "note":
        if not note_text:
            await update.message.reply_text("Usage: note <link|id> your text")
            return
        await apply_note(update, target, note_text)
        return

    status = STATUS_ALIASES.get(action.replace("_", "")) or STATUS_ACTIONS.get(action)
    if not isinstance(status, ListingStatus):
        await update.message.reply_text(f"Unknown action: {action}")
        return

    data = await resolve_target(target)
    await apply_status(update, data, status, header="بروزرسانی شد")


# ── Commands ────────────────────────────────────────────────

HELP_TEXT = """🏠 *House Hunt Bot*

*Save a home*
Send a Divar link → saved to board

*Change status*
`/call <link or id>` — need to call
`/reject <link or id>` — not interested
`/accept <link or id>` — in talk / pursuing
`/bought <link or id>` — purchased
`/wait <link or id>` — waitlist
`/noanswer <link or id>` — called, no answer
`/new <link or id>` — back to new

*Or type without slash:*
`reject https://divar.ir/v/abc`
`call 3`  (by listing ID)

*Reply to a saved ad message:*
`reject` `call` `accept` `bought` `wait` `remove`

*Other*
`/remove <link or id>` — delete from board
`/note <link|id> text` — add note
`/list` — recent listings
`/list reject` — filter by status
`/board` — open dashboard"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(
        "Send a Divar link to save it.\nType /help for all commands.",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def board_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(f"Dashboard: {API_BASE}/")


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return

    status_filter = None
    if context.args:
        key = context.args[0].lower().replace("-", "_")
        if key in STATUS_ALIASES:
            status_filter = STATUS_ALIASES[key].value
        elif key in STATUS_ACTIONS and isinstance(STATUS_ACTIONS[key], ListingStatus):
            status_filter = STATUS_ACTIONS[key].value

    try:
        rows = await api_list(status=status_filter)
    except Exception as exc:
        await update.message.reply_text(f"Could not load list: {exc}")
        return

    if not rows:
        await update.message.reply_text("No listings found.")
        return

    lines = ["Recent listings:\n"]
    for item in rows:
        lines.append(
            f"#{item['id']} [{item['status_label']}] {item['title'][:50]}\n{item['url']}\n"
        )
    await update.message.reply_text("\n".join(lines)[:4000])


def _make_action_handler(action: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        if not context.args:
            await update.message.reply_text(f"Usage: /{action} <link or id>")
            return
        target = " ".join(context.args)
        try:
            await handle_action_on_target(update, action, target)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
        except Exception as exc:
            logger.exception("Command failed")
            await update.message.reply_text(f"Error: {exc}")

    return handler


async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /note <link|id> your note text")
        return
    target = context.args[0]
    note_text = " ".join(context.args[1:])
    try:
        await handle_action_on_target(update, "note", target, note_text=note_text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ── Messages ────────────────────────────────────────────────

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return

    text = (update.message.text or "").strip()

    # Reply to bot message: "reject", "call", etc.
    if update.message.reply_to_message:
        reply_text = (
            update.message.reply_to_message.caption
            or update.message.reply_to_message.text
            or ""
        )
        url = extract_url(reply_text)
        action = text.lower().split()[0]
        if action in STATUS_ALIASES or action in SPECIAL_ACTIONS:
            if not url:
                id_match = re.search(r"(?:ID|شناسه):\s*(\d+)", reply_text)
                target = id_match.group(1) if id_match else reply_text
            else:
                target = url
            note_text = " ".join(text.split()[1:]) if action == "note" else None
            try:
                await handle_action_on_target(update, action, target, note_text=note_text)
            except ValueError as exc:
                await update.message.reply_text(str(exc))
            except Exception as exc:
                await update.message.reply_text(f"Error: {exc}")
            return

    # Text action: "reject https://..."
    action_match = TEXT_ACTION_RE.match(text)
    if action_match:
        action = action_match.group("action").lower()
        target = action_match.group("target").strip()
        note_text = None
        if action == "note":
            parts = target.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text("Usage: note <link|id> your text")
                return
            target, note_text = parts[0], parts[1]
        try:
            await handle_action_on_target(update, action, target, note_text=note_text)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
        except Exception as exc:
            await update.message.reply_text(f"Error: {exc}")
        return

    # Plain link → save
    url = extract_url(text)
    if not url:
        await update.message.reply_text(
            "Send a Divar link, or use commands like:\n"
            "reject <link>\n/call <link>\n/help"
        )
        return

    try:
        data = await api_post_listing(url, str(update.effective_chat.id))
    except httpx.HTTPStatusError as exc:
        await update.message.reply_text(f"Could not save: {exc.response.text}")
        return
    except Exception as exc:
        await update.message.reply_text(f"Could not save: {exc}")
        return

    if data.get("duplicate"):
        await update.message.reply_text(
            "Already on board. Use /reject, /call, /accept etc. to update it.",
        )
        try:
            existing = await api_lookup(url)
            await send_listing_message(update, existing, header="قبلاً ذخیره شده")
        except Exception:
            pass
        return

    await send_listing_message(update, data)


async def _edit_listing_message(query, caption: str, keyboard=None) -> None:
    msg = query.message
    if msg.photo:
        await query.edit_message_caption(caption=caption, reply_markup=keyboard)
    else:
        await query.edit_message_text(text=caption, reply_markup=keyboard, disable_web_page_preview=False)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not await guard(update):
        return

    if query.data.startswith("move:"):
        _, listing_id, status_value = query.data.split(":", 2)
        try:
            data = await api_move(int(listing_id), ListingStatus(status_value))
            label = STATUS_LABELS[ListingStatus(status_value)]
            await _edit_listing_message(
                query,
                _format_caption(data, header=f"بروزرسانی → {label}"),
                status_keyboard(data["id"]),
            )
            await query.answer(f"→ {label}")
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)
        return

    if query.data.startswith("delete:"):
        parts = query.data.split(":")
        listing_id = int(parts[1])
        step = parts[2] if len(parts) > 2 else "ask"

        if step == "ask":
            await query.answer()
            confirm = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("بله، حذف شود", callback_data=f"delete:{listing_id}:yes"),
                    InlineKeyboardButton("انصراف", callback_data=f"delete:{listing_id}:no"),
                ]
            ])
            await query.edit_message_reply_markup(reply_markup=confirm)
            return

        if step == "yes":
            try:
                await api_delete(listing_id)
                await _edit_listing_message(query, f"آگهی #{listing_id} حذف شد", keyboard=None)
                await query.answer("حذف شد")
            except Exception as exc:
                await query.answer(f"Error: {exc}", show_alert=True)
            return

        if step == "no":
            await query.answer("لغو شد")
            await query.edit_message_reply_markup(reply_markup=status_keyboard(listing_id))


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("board", board_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("note", note_cmd))

    for cmd in ("call", "reject", "accept", "bought", "wait", "noanswer", "new", "remove"):
        application.add_handler(CommandHandler(cmd, _make_action_handler(cmd)))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.add_handler(CallbackQueryHandler(handle_callback))


def build_app() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")

    application = Application.builder().token(settings.telegram_bot_token).build()
    register_handlers(application)
    return application


def main() -> None:
    init_db()
    app = build_app()
    logger.info("Telegram bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
