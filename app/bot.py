from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.database import ListingStatus, STATUS_LABELS
from app.services import extract_url

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_BASE = settings.dashboard_url.rstrip("/")
LIST_PAGE_SIZE = 5

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

REPLY_MENU = {
    "📋 لیست آگهی‌ها": "list",
    "📊 خلاصه برد": "stats",
    "🌐 داشبورد": "board",
    "❓ راهنما": "help",
}

STATUS_ICONS = {
    ListingStatus.NEW: "✦",
    ListingStatus.NEED_CALL: "📞",
    ListingStatus.NO_ANSWER: "🔇",
    ListingStatus.WAITLIST: "⏳",
    ListingStatus.IN_TALK: "💬",
    ListingStatus.REJECTED: "❌",
    ListingStatus.BOUGHT: "🏠",
}


def allowed(user_id: int) -> bool:
    allowed_ids = settings.allowed_user_ids
    return not allowed_ids or user_id in allowed_ids


async def guard(update: Update) -> bool:
    if not update.effective_user or not allowed(update.effective_user.id):
        if update.message:
            await update.message.reply_text("اجازه استفاده از این ربات را ندارید.")
        elif update.callback_query:
            await update.callback_query.answer("اجازه ندارید", show_alert=True)
        return False
    return True


# ── API client ──────────────────────────────────────────────

async def api_request(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20.0) as client:
        return await client.request(method, f"{API_BASE}{path}", **kwargs)


async def api_post_listing(url: str, chat_id: str) -> dict:
    response = await api_request(
        "POST",
        "/api/listings",
        json={"url": url, "source_chat_id": chat_id},
    )
    response.raise_for_status()
    data = response.json()
    data["_updated"] = response.status_code == 200
    return data


async def api_lookup(query: str) -> dict:
    response = await api_request("GET", "/api/lookup", params={"q": query})
    response.raise_for_status()
    return response.json()


async def api_get_listing(listing_id: int) -> dict:
    response = await api_request("GET", f"/api/listings/{listing_id}")
    response.raise_for_status()
    return response.json()


async def api_kanban() -> dict:
    response = await api_request("GET", "/api/kanban")
    response.raise_for_status()
    return response.json()


async def api_refresh(listing_id: int) -> dict:
    response = await api_request("POST", f"/api/listings/{listing_id}/refresh")
    response.raise_for_status()
    return response.json()


def _api_error_message(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 404:
            return "این آگهی در برد نیست. لینک را دوباره بفرستید یا /list بزنید."
        if exc.response.status_code == 409:
            return "این آگهی قبلاً ذخیره شده."
        try:
            detail = exc.response.json().get("detail")
            if detail:
                return str(detail)
        except Exception:
            pass
        return f"خطای سرور ({exc.response.status_code})"
    return str(exc)


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


async def api_list(status: Optional[str] = None, limit: int = 30) -> list[dict]:
    params = {"limit": limit}
    if status:
        params["status"] = status
    response = await api_request("GET", "/api/listings", params=params)
    response.raise_for_status()
    return response.json()


async def _get_listing_for_callback(query, listing_id: int) -> dict:
    """Fetch listing by id; if stale, resolve from Divar URL in the message."""
    try:
        return await api_get_listing(listing_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        msg = query.message
        if not msg:
            raise
        text = msg.caption or msg.text or ""
        url = extract_url(text)
        if url:
            return await api_lookup(url)
        raise ValueError(
            "این آگهی در برد نیست (شناسه قدیمی است). لینک را دوباره بفرستید یا /list بزنید."
        ) from exc


# ── Formatting & keyboards ──────────────────────────────────

def _status_label(data: dict) -> str:
    status = data.get("status")
    if status:
        try:
            return data.get("status_label") or STATUS_LABELS[ListingStatus(status)]
        except ValueError:
            pass
    return data.get("status_label") or "—"


def _format_specs_line(specs: dict, limit: int = 6) -> str:
    keys = ["متراژ", "اتاق", "طبقه", "ساخت", "قیمت کل", "قیمت هر متر", "ودیعه", "اجاره"]
    parts = []
    for key in keys:
        if key in specs:
            parts.append(f"• {key}: {specs[key]}")
    if not parts:
        for key, value in list(specs.items())[:limit]:
            parts.append(f"• {key}: {value}")
    return "\n".join(parts[:limit])


def _format_caption(data: dict, header: str = "ذخیره شد در برد") -> str:
    lines = [header, "", f"🏠 {data.get('title', 'آگهی')}"]
    if data.get("location"):
        lines.append(f"📍 {data['location']}")
    if data.get("price"):
        lines.append(f"💰 {data['price']}")
    spec_line = _format_specs_line(data.get("specs") or {}, limit=4)
    if spec_line:
        lines.extend(["", spec_line])
    lines.extend(["", f"📌 وضعیت: {_status_label(data)}", f"🆔 شناسه: {data.get('id', '—')}"])
    caption = "\n".join(lines)
    return caption[:1020] + ("..." if len(caption) > 1024 else "")


def _format_detail(data: dict) -> str:
    lines = [
        f"🏠 *{data.get('title', 'آگهی')}*",
        "",
        f"📌 وضعیت: {_status_label(data)}",
        f"🆔 شناسه: {data.get('id', '—')}",
    ]
    if data.get("location"):
        lines.append(f"📍 {data['location']}")
    if data.get("price"):
        lines.append(f"💰 {data['price']}")

    specs = data.get("specs") or {}
    if specs:
        lines.extend(["", "*مشخصات*", _format_specs_line(specs)])

    notes = (data.get("notes") or "").strip()
    if notes:
        lines.extend(["", "*یادداشت*", notes[:400]])

    desc = (data.get("description") or "").strip()
    if desc:
        short = desc[:500] + ("..." if len(desc) > 500 else "")
        lines.extend(["", "*توضیحات*", short])

    images = data.get("images") or []
    if images:
        lines.append(f"\n🖼 {len(images)} عکس")

    if data.get("url"):
        lines.extend(["", data["url"]])

    text = "\n".join(lines)
    return text[:3900] + ("..." if len(text) > 3900 else "")


def reply_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📋 لیست آگهی‌ها", "📊 خلاصه برد"],
            ["🌐 داشبورد", "❓ راهنما"],
        ],
        resize_keyboard=True,
    )


def main_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 لیست آگهی‌ها", callback_data="menu:list:0:all"),
            InlineKeyboardButton("📊 خلاصه برد", callback_data="menu:stats"),
        ],
        [
            InlineKeyboardButton("🌐 داشبورد", url=f"{API_BASE}/"),
            InlineKeyboardButton("❓ راهنما", callback_data="menu:help"),
        ],
    ])


def status_keyboard(listing_id: int) -> InlineKeyboardMarkup:
    buttons = [
        (ListingStatus.NEED_CALL, f"📞 {STATUS_LABELS[ListingStatus.NEED_CALL]}"),
        (ListingStatus.NO_ANSWER, f"🔇 {STATUS_LABELS[ListingStatus.NO_ANSWER]}"),
        (ListingStatus.IN_TALK, f"✅ {STATUS_LABELS[ListingStatus.IN_TALK]}"),
        (ListingStatus.WAITLIST, f"⏳ {STATUS_LABELS[ListingStatus.WAITLIST]}"),
        (ListingStatus.REJECTED, f"❌ {STATUS_LABELS[ListingStatus.REJECTED]}"),
        (ListingStatus.BOUGHT, f"🏠 {STATUS_LABELS[ListingStatus.BOUGHT]}"),
    ]
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("👁 جزئیات", callback_data=f"show:{listing_id}"),
            InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"refresh:{listing_id}"),
        ],
        [
            InlineKeyboardButton("🔗 دیوار", url=""),  # placeholder, set below
        ],
    ]
    # Divar link row filled when sending
    rows = rows[:1]
    row: list[InlineKeyboardButton] = []
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


def listing_keyboard(data: dict) -> InlineKeyboardMarkup:
    listing_id = data["id"]
    buttons = [
        (ListingStatus.NEED_CALL, f"📞 {STATUS_LABELS[ListingStatus.NEED_CALL]}"),
        (ListingStatus.NO_ANSWER, f"🔇 {STATUS_LABELS[ListingStatus.NO_ANSWER]}"),
        (ListingStatus.IN_TALK, f"✅ {STATUS_LABELS[ListingStatus.IN_TALK]}"),
        (ListingStatus.WAITLIST, f"⏳ {STATUS_LABELS[ListingStatus.WAITLIST]}"),
        (ListingStatus.REJECTED, f"❌ {STATUS_LABELS[ListingStatus.REJECTED]}"),
        (ListingStatus.BOUGHT, f"🏠 {STATUS_LABELS[ListingStatus.BOUGHT]}"),
    ]
    rows: list[list[InlineKeyboardButton]] = [[
        InlineKeyboardButton("👁 جزئیات", callback_data=f"show:{listing_id}"),
        InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"refresh:{listing_id}"),
    ]]
    if data.get("url"):
        rows[0].append(InlineKeyboardButton("🔗 دیوار", url=data["url"]))
    row: list[InlineKeyboardButton] = []
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


def detail_keyboard(listing_id: int, url: Optional[str] = None) -> InlineKeyboardMarkup:
    row1 = [
        InlineKeyboardButton("◀️ برگشت", callback_data=f"showcard:{listing_id}"),
        InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"refresh:{listing_id}"),
    ]
    rows = [row1]
    if url:
        rows.append([InlineKeyboardButton("🔗 باز کردن در دیوار", url=url)])
    rows.append([InlineKeyboardButton("📋 برد", url=f"{API_BASE}/")])
    return InlineKeyboardMarkup(rows)


def list_filter_keyboard(active: str = "all") -> list[list[InlineKeyboardButton]]:
    filters = [
        ("all", "همه"),
        ("new", "جدید"),
        ("need_call", "تماس"),
        ("in_talk", "پیگیری"),
        ("waitlist", "انتظار"),
        ("rejected", "رد"),
    ]
    row: list[InlineKeyboardButton] = []
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in filters:
        prefix = "• " if key == active else ""
        row.append(InlineKeyboardButton(f"{prefix}{label}", callback_data=f"menu:list:0:{key}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def list_page_keyboard(rows: list[dict], page: int, status: str) -> InlineKeyboardMarkup:
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    start = page * LIST_PAGE_SIZE
    chunk = rows[start : start + LIST_PAGE_SIZE]

    for item in chunk:
        icon = STATUS_ICONS.get(ListingStatus(item["status"]), "•")
        title = item["title"][:28] + ("…" if len(item["title"]) > 28 else "")
        keyboard_rows.append([
            InlineKeyboardButton(
                f"{icon} #{item['id']} — {title}",
                callback_data=f"showcard:{item['id']}",
            )
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"menu:list:{page - 1}:{status}"))
    if start + LIST_PAGE_SIZE < len(rows):
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"menu:list:{page + 1}:{status}"))
    if nav:
        keyboard_rows.append(nav)

    keyboard_rows.extend(list_filter_keyboard(status))
    keyboard_rows.append([
        InlineKeyboardButton("🏠 منو", callback_data="menu:home"),
        InlineKeyboardButton("🌐 داشبورد", url=f"{API_BASE}/"),
    ])
    return InlineKeyboardMarkup(keyboard_rows)


# ── Send helpers ────────────────────────────────────────────

async def _reply_target(update: Update):
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        await update.callback_query.answer()
        return update.callback_query.message
    return None


async def send_listing_card(update: Update, data: dict, header: str = "ذخیره شد در برد") -> None:
    sender = await _reply_target(update)
    if not sender:
        return
    caption = _format_caption(data, header)
    keyboard = listing_keyboard(data)
    image_url = data.get("image_url")
    if image_url:
        await sender.reply_photo(photo=image_url, caption=caption, reply_markup=keyboard)
    else:
        await sender.reply_text(caption, reply_markup=keyboard, disable_web_page_preview=False)


async def send_listing_detail(update: Update, data: dict, *, edit: bool = False) -> None:
    text = _format_detail(data)
    keyboard = detail_keyboard(data["id"], data.get("url"))
    image_url = data.get("image_url")

    if edit and update.callback_query and update.callback_query.message:
        msg = update.callback_query.message
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await msg.edit_text(text=text, reply_markup=keyboard, parse_mode="Markdown", disable_web_page_preview=False)
        return

    sender = await _reply_target(update)
    if not sender:
        return
    if image_url:
        await sender.reply_photo(photo=image_url, caption=text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await sender.reply_text(text, reply_markup=keyboard, parse_mode="Markdown", disable_web_page_preview=False)


async def send_interactive_list(
    update: Update,
    *,
    page: int = 0,
    status: str = "all",
    edit: bool = False,
) -> None:
    status_filter = None if status == "all" else status
    try:
        rows = await api_list(status=status_filter)
    except Exception as exc:
        text = f"خطا در بارگذاری لیست:\n{_api_error_message(exc)}"
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        elif update.message:
            await update.message.reply_text(text)
        return

    if not rows:
        text = "آگهی‌ای نیست.\nلینک دیوار بفرستید تا ذخیره شود."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منو", callback_data="menu:home")]])
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        elif update.message:
            await update.message.reply_text(text, reply_markup=kb)
        return

    total_pages = max(1, (len(rows) + LIST_PAGE_SIZE - 1) // LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    filter_label = "همه" if status == "all" else STATUS_LABELS.get(ListingStatus(status), status)
    text = f"📋 *لیست آگهی‌ها* ({len(rows)} مورد)\nفیلتر: {filter_label} · صفحه {page + 1}/{total_pages}\n\nروی هر آگهی بزنید:"
    keyboard = list_page_keyboard(rows, page, status)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def send_board_stats(update: Update, *, edit: bool = False) -> None:
    try:
        board = await api_kanban()
    except Exception as exc:
        text = f"خطا:\n{_api_error_message(exc)}"
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        elif update.message:
            await update.message.reply_text(text)
        return

    lines = ["📊 *خلاصه برد*", ""]
    active = 0
    for col in board["columns"]:
        count = len(col["listings"])
        if col["status"] not in ("rejected", "bought"):
            active += count
        icon = STATUS_ICONS.get(ListingStatus(col["status"]), "•")
        lines.append(f"{icon} {col['label']}: *{count}*")

    lines.extend(["", f"📦 کل: *{board['total']}* · فعال: *{active}*"])
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 لیست", callback_data="menu:list:0:all"),
            InlineKeyboardButton("🌐 داشبورد", url=f"{API_BASE}/"),
        ],
        [InlineKeyboardButton("🏠 منو", callback_data="menu:home")],
    ])

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ── Core actions ────────────────────────────────────────────

async def resolve_target(target: str) -> dict:
    target = target.strip()
    url = extract_url(target)
    try:
        return await api_lookup(url or target)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ValueError(f"آگهی پیدا نشد: {target}") from exc
        raise


async def apply_status(update: Update, data: dict, status: ListingStatus, header: str) -> None:
    updated = await api_move(data["id"], status)
    await send_listing_card(update, updated, header=f"{header} → {STATUS_LABELS[status]}")


async def apply_delete(update: Update, listing_id: int) -> None:
    await api_delete(listing_id)
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        await msg.reply_text(f"🗑 آگهی #{listing_id} از برد حذف شد.")


async def apply_note(update: Update, target: str, note_text: str) -> None:
    data = await resolve_target(target)
    existing = (data.get("notes") or "").strip()
    merged = f"{existing}\n{note_text}".strip() if existing else note_text
    updated = await api_patch(data["id"], notes=merged)
    await send_listing_card(update, updated, header="یادداشت ذخیره شد")


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
            await update.message.reply_text("نحوه استفاده: /note <شناسه|لینک> متن یادداشت")
            return
        await apply_note(update, target, note_text)
        return

    status = STATUS_ALIASES.get(action.replace("_", "")) or STATUS_ACTIONS.get(action)
    if not isinstance(status, ListingStatus):
        await update.message.reply_text(f"دستور ناشناخته: {action}")
        return

    data = await resolve_target(target)
    await apply_status(update, data, status, header="بروزرسانی شد")


# ── Commands ────────────────────────────────────────────────

HELP_TEXT = """🏠 *ربات پیگیری خانه*

*ذخیره آگهی*
لینک دیوار بفرستید → در برد ذخیره می‌شود

*دستورات*
/show <شناسه> — جزئیات کامل آگهی
/list — لیست تعاملی آگهی‌ها
/stats — خلاصه برد
/board — لینک داشبورد

*تغییر وضعیت*
/call /reject /accept /bought /wait /noanswer /new /remove

*میانبر*
`call 1` یا `reject https://divar.ir/v/...`

*روی پیام آگهی ریپلای کنید:*
`call` `reject` `accept` `note متن...`

*یادداشت*
/note <شناسه|لینک> متن یادداشت"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(
        "🏠 *سلام! به ربات پیگیری خانه خوش آمدید*\n\n"
        "لینک دیوار بفرستید تا ذخیره شود.\n"
        "از دکمه‌های زیر یا منوی پایین استفاده کنید.",
        reply_markup=reply_menu_keyboard(),
        parse_mode="Markdown",
    )
    await update.message.reply_text("منوی سریع:", reply_markup=main_menu_inline())


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown", reply_markup=main_menu_inline())


async def board_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await update.message.reply_text(
        f"🌐 داشبورد:\n{API_BASE}/",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("باز کردن داشبورد", url=f"{API_BASE}/")],
        ]),
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    await send_board_stats(update)


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    status = "all"
    if context.args:
        key = context.args[0].lower().replace("-", "_")
        if key in STATUS_ALIASES:
            status = STATUS_ALIASES[key].value
        elif key in STATUS_ACTIONS and isinstance(STATUS_ACTIONS[key], ListingStatus):
            status = STATUS_ACTIONS[key].value
    await send_interactive_list(update, status=status)


async def show_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("نحوه استفاده: /show <شناسه>\nمثال: /show 1")
        return
    try:
        data = await api_get_listing(int(context.args[0]))
        await send_listing_detail(update, data)
    except ValueError:
        await update.message.reply_text("شناسه باید عدد باشد.")
    except Exception as exc:
        await update.message.reply_text(_api_error_message(exc))


def _make_action_handler(action: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard(update):
            return
        if not context.args:
            await update.message.reply_text(f"نحوه استفاده: /{action} <شناسه|لینک>")
            return
        target = " ".join(context.args)
        try:
            await handle_action_on_target(update, action, target)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
        except Exception as exc:
            logger.exception("Command failed")
            await update.message.reply_text(_api_error_message(exc))

    return handler


async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("نحوه استفاده: /note <شناسه|لینک> متن یادداشت")
        return
    target = context.args[0]
    note_text = " ".join(context.args[1:])
    try:
        await handle_action_on_target(update, "note", target, note_text=note_text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
    except Exception as exc:
        await update.message.reply_text(_api_error_message(exc))


# ── Messages ────────────────────────────────────────────────

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard(update):
        return

    text = (update.message.text or "").strip()

    if text in REPLY_MENU:
        action = REPLY_MENU[text]
        if action == "list":
            await send_interactive_list(update)
        elif action == "stats":
            await send_board_stats(update)
        elif action == "board":
            await board_cmd(update, context)
        elif action == "help":
            await help_cmd(update, context)
        return

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
                await update.message.reply_text(_api_error_message(exc))
            return

    action_match = TEXT_ACTION_RE.match(text)
    if action_match:
        action = action_match.group("action").lower()
        target = action_match.group("target").strip()
        note_text = None
        if action == "note":
            parts = target.split(maxsplit=1)
            if len(parts) < 2:
                await update.message.reply_text("نحوه استفاده: note <شناسه|لینک> متن")
                return
            target, note_text = parts[0], parts[1]
        try:
            await handle_action_on_target(update, action, target, note_text=note_text)
        except ValueError as exc:
            await update.message.reply_text(str(exc))
        except Exception as exc:
            await update.message.reply_text(_api_error_message(exc))
        return

    url = extract_url(text)
    if not url:
        await update.message.reply_text(
            "لینک دیوار بفرستید یا از منو استفاده کنید.\n/help برای راهنما",
            reply_markup=main_menu_inline(),
        )
        return

    try:
        data = await api_post_listing(url, str(update.effective_chat.id))
    except httpx.HTTPStatusError as exc:
        await update.message.reply_text(f"ذخیره نشد: {_api_error_message(exc)}")
        return
    except Exception as exc:
        await update.message.reply_text(f"ذخیره نشد: {exc}")
        return

    header = "بروزرسانی شد (در برد بود)" if data.pop("_updated", False) else "ذخیره شد در برد"
    await send_listing_card(update, data, header=header)


async def _edit_listing_message(query, caption: str, keyboard=None, parse_mode: Optional[str] = None) -> None:
    msg = query.message
    if msg.photo:
        await msg.edit_caption(caption=caption, reply_markup=keyboard, parse_mode=parse_mode)
    else:
        await msg.edit_message_text(
            text=caption, reply_markup=keyboard, parse_mode=parse_mode, disable_web_page_preview=False
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if not await guard(update):
        return

    data = query.data

    if data == "menu:home":
        await query.answer()
        await query.edit_message_text(
            "🏠 *منوی اصلی*\nلینک دیوار بفرستید یا یک گزینه انتخاب کنید:",
            reply_markup=main_menu_inline(),
            parse_mode="Markdown",
        )
        return

    if data == "menu:help":
        await query.answer()
        await query.edit_message_text(HELP_TEXT, parse_mode="Markdown", reply_markup=main_menu_inline())
        return

    if data == "menu:stats":
        await query.answer()
        await send_board_stats(update, edit=True)
        return

    if data.startswith("menu:list:"):
        await query.answer()
        _, _, page_s, status = data.split(":", 3)
        await send_interactive_list(update, page=int(page_s), status=status, edit=True)
        return

    if data.startswith("showcard:"):
        await query.answer()
        listing_id = int(data.split(":")[1])
        try:
            listing = await _get_listing_for_callback(query, listing_id)
            caption = _format_caption(listing, header="کارت آگهی")
            await _edit_listing_message(query, caption, listing_keyboard(listing))
        except Exception as exc:
            await query.answer(_api_error_message(exc), show_alert=True)
        return

    if data.startswith("show:"):
        await query.answer()
        listing_id = int(data.split(":")[1])
        try:
            listing = await _get_listing_for_callback(query, listing_id)
            await send_listing_detail(update, listing, edit=True)
        except Exception as exc:
            await query.answer(_api_error_message(exc), show_alert=True)
        return

    if data.startswith("refresh:"):
        listing_id = int(data.split(":")[1])
        try:
            listing = await _get_listing_for_callback(query, listing_id)
            listing = await api_refresh(listing["id"])
            await query.answer("بروزرسانی شد ✓")
            caption = _format_caption(listing, header="بروزرسانی از دیوار")
            await _edit_listing_message(query, caption, listing_keyboard(listing))
        except Exception as exc:
            await query.answer(_api_error_message(exc), show_alert=True)
        return

    if data.startswith("move:"):
        _, listing_id, status_value = data.split(":", 2)
        try:
            current = await _get_listing_for_callback(query, int(listing_id))
            listing = await api_move(current["id"], ListingStatus(status_value))
            label = STATUS_LABELS[ListingStatus(status_value)]
            await _edit_listing_message(
                query,
                _format_caption(listing, header=f"بروزرسانی → {label}"),
                listing_keyboard(listing),
            )
            await query.answer(f"→ {label}")
        except Exception as exc:
            await query.answer(_api_error_message(exc), show_alert=True)
        return

    if data.startswith("delete:"):
        parts = data.split(":")
        listing_id = int(parts[1])
        step = parts[2] if len(parts) > 2 else "ask"

        if step == "ask":
            await query.answer()
            try:
                listing = await _get_listing_for_callback(query, listing_id)
                listing_id = listing["id"]
            except Exception as exc:
                await query.answer(_api_error_message(exc), show_alert=True)
                return
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
                listing = await _get_listing_for_callback(query, listing_id)
                await api_delete(listing["id"])
                await _edit_listing_message(query, f"🗑 آگهی #{listing['id']} حذف شد", keyboard=None)
                await query.answer("حذف شد")
            except Exception as exc:
                await query.answer(_api_error_message(exc), show_alert=True)
            return

        if step == "no":
            await query.answer("لغو شد")
            try:
                listing = await _get_listing_for_callback(query, listing_id)
                await query.edit_message_reply_markup(reply_markup=listing_keyboard(listing))
            except Exception:
                pass


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("board", board_cmd))
    application.add_handler(CommandHandler("list", list_cmd))
    application.add_handler(CommandHandler("show", show_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
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
    app = build_app()
    logger.info("Telegram bot polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
