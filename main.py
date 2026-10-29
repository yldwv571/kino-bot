import asyncio
import html
import logging
import os
import re
import sqlite3

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import (
    JOIN_TRANSITION,
    ChatMemberUpdatedFilter,
    Command,
    CommandObject,
    CommandStart,
    StateFilter,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    CallbackQuery,
    ChatMemberUpdated,
    MenuButtonCommands,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===================== SOZLAMALAR =====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOT_TOKENNI_SHU_YERGA_YOZING")
# Admin ID lari (vergul bilan ajrating). ID ni @userinfobot dan oling yoki botga /id yozing
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",") if x.strip()
}
# Majburiy obuna kanallari. Bo'sh qoldirsangiz o'chiq. Masalan: ["@kanal1", "@kanal2"]
# Bot shu kanallarda ADMIN bo'lishi kerak!
CHANNELS: list[str] = []
DB_PATH = os.getenv("DB_PATH", "kino.db")
# ======================================================

logging.basicConfig(level=logging.INFO)
BOT_USERNAME = ""

# ===================== BUYRUQLAR RO'YXATI ("/" bosilganda chiqadi) =====================
USER_COMMANDS = [
    BotCommand(command="start", description="🚀 Botni ishga tushirish"),
    BotCommand(command="help", description="📖 Yordam va buyruqlar"),
    BotCommand(command="top", description="🔥 Eng ko'p ko'rilgan kinolar"),
    BotCommand(command="new", description="🆕 Oxirgi qo'shilgan kinolar"),
    BotCommand(command="random", description="🎲 Tasodifiy kino"),
    BotCommand(command="search", description="🔎 Kinoni nomi bilan qidirish"),
    BotCommand(command="id", description="🆔 ID raqamingiz"),
    BotCommand(command="about", description="ℹ️ Bot haqida"),
]
GROUP_COMMANDS = [
    BotCommand(command="start", description="🚀 Botni ishga tushirish"),
    BotCommand(command="help", description="📖 Yordam va buyruqlar"),
    BotCommand(command="top", description="🔥 Eng ko'p ko'rilgan kinolar"),
    BotCommand(command="new", description="🆕 Oxirgi qo'shilgan kinolar"),
    BotCommand(command="random", description="🎲 Tasodifiy kino"),
    BotCommand(command="search", description="🔎 Kinoni nomi bilan qidirish"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="admin", description="⚙️ Admin panel"),
    BotCommand(command="add", description="➕ Kino qo'shish"),
    BotCommand(command="del", description="🗑 Kino o'chirish"),
    BotCommand(command="cancel", description="❌ Bekor qilish"),
]


# ===================== BAZA =====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS movies (
                code INTEGER PRIMARY KEY,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                title TEXT NOT NULL,
                views INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT
            )"""
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, title TEXT)"
        )
        await db.commit()


async def add_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, full_name, username) VALUES (?, ?, ?)",
            (user.id, user.full_name, user.username),
        )
        await db.commit()


async def add_chat(chat):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chats (chat_id, title) VALUES (?, ?)",
            (chat.id, chat.title or ""),
        )
        await db.commit()


async def next_code() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(MAX(code), 0) + 1 FROM movies")
        return (await cur.fetchone())[0]


async def movie_title(code: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title FROM movies WHERE code = ?", (code,))
        row = await cur.fetchone()
        return row[0] if row else None


async def add_movie(code: int, file_id: str, file_type: str, title: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO movies (code, file_id, file_type, title) VALUES (?, ?, ?, ?)",
                (code, file_id, file_type, title),
            )
            await db.commit()
            return True
        except sqlite3.IntegrityError:
            return False


async def get_movie(code: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT code, file_id, file_type, title, views FROM movies WHERE code = ?",
            (code,),
        )
        row = await cur.fetchone()
        if row:
            await db.execute("UPDATE movies SET views = views + 1 WHERE code = ?", (code,))
            await db.commit()
        return row


async def delete_movie(code: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM movies WHERE code = ?", (code,))
        await db.commit()
        return cur.rowcount > 0


async def list_movies():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT code, title, views FROM movies ORDER BY code")
        return await cur.fetchall()


async def top_movies(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT code, title, views FROM movies ORDER BY views DESC, code LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def new_movies(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT code, title, views FROM movies ORDER BY rowid DESC LIMIT ?", (limit,)
        )
        return await cur.fetchall()


async def search_movies(query: str, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT code, title, views FROM movies WHERE title LIKE ? "
            "ORDER BY views DESC LIMIT ?",
            (f"%{query}%", limit),
        )
        return await cur.fetchall()


async def random_code():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT code FROM movies ORDER BY RANDOM() LIMIT 1")
        row = await cur.fetchone()
        return row[0] if row else None


async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        chats = (await (await db.execute("SELECT COUNT(*) FROM chats")).fetchone())[0]
        movies = (await (await db.execute("SELECT COUNT(*) FROM movies")).fetchone())[0]
        views = (
            await (await db.execute("SELECT COALESCE(SUM(views), 0) FROM movies")).fetchone()
        )[0]
        return users, chats, movies, views


async def all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return [r[0] for r in await cur.fetchall()]


# ===================== YORDAMCHI =====================
def is_admin_id(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def not_subscribed(bot: Bot, user_id: int) -> list[str]:
    missing = []
    for ch in CHANNELS:
        try:
            m = await bot.get_chat_member(ch, user_id)
            if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                missing.append(ch)
        except Exception as e:
            logging.warning("Obunani tekshirib bo'lmadi (%s): %s", ch, e)
    return missing


async def ensure_sub(message: Message, bot: Bot) -> bool:
    """Faqat shaxsiy chatda majburiy obunani tekshiradi."""
    if message.chat.type != ChatType.PRIVATE or not CHANNELS:
        return True
    if is_admin_id(message.from_user.id):
        return True
    if await not_subscribed(bot, message.from_user.id):
        await message.answer(
            "❗ Botdan foydalanish uchun kanallarga obuna bo'ling:", reply_markup=sub_kb()
        )
        return False
    return True


def sub_kb():
    b = InlineKeyboardBuilder()
    for ch in CHANNELS:
        b.button(text=f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}")
    b.button(text="✅ Tekshirish", callback_data="check_sub")
    b.adjust(1)
    return b.as_markup()


def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="➕ Kino qo'shish", callback_data="adm:add")
    b.button(text="🗑 Kino o'chirish", callback_data="adm:del")
    b.button(text="📋 Kinolar ro'yxati", callback_data="adm:list")
    b.button(text="📊 Statistika", callback_data="adm:stats")
    b.button(text="📢 Xabar yuborish", callback_data="adm:bc")
    b.button(text="⬅️ Orqaga", callback_data="u:back")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


def cancel_kb():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Bekor qilish", callback_data="adm:cancel")
    return b.as_markup()


def back_kb():
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Orqaga", callback_data="u:back")
    return b.as_markup()


def start_kb(admin: bool):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Guruhga qo'shish", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")
    b.button(text="📖 Buyruqlar", callback_data="u:help")
    b.button(text="ℹ️ Bot haqida", callback_data="u:about")
    b.button(text="🔥 Top kinolar", callback_data="u:top")
    b.button(text="🆕 Yangi kinolar", callback_data="u:new")
    if admin:
        b.button(text="⚙️ Admin panel", callback_data="adm:panel")
        b.adjust(1, 2, 2, 1)
    else:
        b.adjust(1, 2, 2)
    return b.as_markup()


def movie_caption(code: int, title: str, views: int) -> str:
    return (
        f"🎬 <b>{html.escape(title)}</b>\n\n"
        f"🔢 Kod: <b>{code}</b>\n"
        f"👁 Ko'rishlar: {views}\n\n"
        f"🤖 @{BOT_USERNAME}"
    )


def start_text(name: str) -> str:
    return (
        f"⭐ Salom, <b>{html.escape(name)}</b>!\n\n"
        "Men <b>KINO BOT</b> — sevimli kinolaringizni kod orqali tez topib "
        "beruvchi botman.\n\n"
        "🔥 <b>Imkoniyatlar:</b>\n"
        "<blockquote>• Kino kodini (raqam) yuboring — kino darhol keladi\n"
        "• Kinoni nomi bilan qidirish\n"
        "• Eng ko'p ko'rilgan va yangi kinolar ro'yxati\n"
        "• Tasodifiy kino tavsiyasi\n"
        "• Guruhlarda ham ishlaydi\n"
        "• Doimiy yangi kinolar qo'shib boriladi</blockquote>\n\n"
        "Boshlash uchun kino kodini yuboring yoki meni guruhga qo'shing 👇"
    )


GROUP_TEXT = (
    "🎬 <b>Kino bot</b> guruhda tayyor!\n\n"
    "Kino kodini (raqam) yozing — kino shu guruhning o'ziga keladi.\n"
    "Masalan: <code>1</code>\n\n"
    "🔥 /top  🆕 /new  🎲 /random  🔎 /search nom\n\n"
    "To'liq imkoniyatlar uchun botga o'ting 👇"
)

HELP_TEXT = (
    "📖 <b>Buyruqlar</b>\n\n"
    "/start — botni ishga tushirish\n"
    "/help — yordam\n"
    "/top — eng ko'p ko'rilgan kinolar\n"
    "/new — oxirgi qo'shilgan kinolar\n"
    "/random — tasodifiy kino\n"
    "/search nom — kinoni nomi bilan qidirish\n"
    "/id — ID raqamingiz\n"
    "/about — bot haqida\n\n"
    "🔢 Kino olish uchun shunchaki <b>kino kodini</b> yuboring. Masalan: <code>1</code>"
)

ADMIN_HELP = (
    "\n\n⚙️ <b>Admin buyruqlari</b>\n"
    "/admin — admin panel\n"
    "/add — kino qo'shish\n"
    "/del 5 — 5-kodli kinoni o'chirish\n"
    "/cancel — amalni bekor qilish\n\n"
    "⚡ <b>Tezkor yuklash:</b> videoni yuboring va izohiga (caption) "
    "<code>5 Kino nomi</code> deb yozing — kino 5-kod bilan darhol saqlanadi."
)

ABOUT_TEXT = (
    "ℹ️ <b>Bot haqida</b>\n\n"
    "🎬 Bu bot orqali kinolarni kod yordamida tez topasiz.\n"
    "Kodni yuborasiz — kino keladi. Shaxsiy chatda ham, guruhda ham ishlaydi.\n\n"
    "📌 Guruhga qo'shish uchun <b>«Guruhga qo'shish»</b> tugmasini bosing."
)


def fmt_rows(rows) -> str:
    return "\n".join(
        f"{i}. <b>{c}</b> — {html.escape(t)} (👁 {v})" for i, (c, t, v) in enumerate(rows, 1)
    )


async def top_text() -> str:
    rows = await top_movies(10)
    if not rows:
        return "📭 Hozircha kino yo'q."
    return "🔥 <b>Top kinolar</b>\n\n" + fmt_rows(rows) + "\n\n🔢 Kino olish uchun kodini yuboring."


async def new_text() -> str:
    rows = await new_movies(10)
    if not rows:
        return "📭 Hozircha kino yo'q."
    return "🆕 <b>Yangi kinolar</b>\n\n" + fmt_rows(rows) + "\n\n🔢 Kino olish uchun kodini yuboring."


async def set_admin_commands(bot: Bot, chat_id: int):
    try:
        await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=chat_id))
    except Exception as e:
        logging.warning("Admin buyruqlarini o'rnatib bo'lmadi (%s): %s", chat_id, e)


async def setup_commands(bot: Bot):
    try:
        await bot.set_my_commands(USER_COMMANDS)
        await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logging.warning("Buyruqlarni o'rnatishda xato: %s", e)
    for admin_id in ADMIN_IDS:
        await set_admin_commands(bot, admin_id)


def extract_file(message: Message):
    if message.video:
        return message.video.file_id, "video"
    if message.document:
        return message.document.file_id, "document"
    return None, None


# ===================== ADMIN =====================
admin_router = Router()
admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS), F.chat.type == ChatType.PRIVATE)
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

CAPTION_RE = re.compile(r"^\s*(\d{1,9})\s+(.+)$", re.S)


class AddMovie(StatesGroup):
    video = State()
    code = State()
    title = State()


class DelMovie(StatesGroup):
    code = State()


class Broadcast(StatesGroup):
    msg = State()


ADD_VIDEO_TEXT = (
    "🎬 Kinoni (video yoki fayl) yuboring.\n\n"
    "💡 Tezroq usul: videoni izoh bilan yuboring: <code>5 Kino nomi</code>"
)


@admin_router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await set_admin_commands(bot, message.from_user.id)
    await message.answer("⚙️ <b>Admin panel</b>", reply_markup=admin_kb())


@admin_router.callback_query(F.data == "adm:panel")
async def admin_panel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_text("⚙️ <b>Admin panel</b>", reply_markup=admin_kb())
    except TelegramBadRequest:
        pass
    await call.answer()


@admin_router.message(Command("cancel"), StateFilter("*"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Bekor qilindi.", reply_markup=admin_kb())


@admin_router.callback_query(F.data == "adm:cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("❌ Bekor qilindi.", reply_markup=admin_kb())
    await call.answer()


# --- kino qo'shish ---
@admin_router.message(Command("add"))
async def add_cmd(message: Message, state: FSMContext):
    await state.set_state(AddMovie.video)
    await message.answer(ADD_VIDEO_TEXT, reply_markup=cancel_kb())


@admin_router.callback_query(F.data == "adm:add")
async def add_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddMovie.video)
    await call.message.answer(ADD_VIDEO_TEXT, reply_markup=cancel_kb())
    await call.answer()


@admin_router.message(AddMovie.video, F.video | F.document)
async def add_video(message: Message, state: FSMContext):
    file_id, file_type = extract_file(message)
    await state.update_data(file_id=file_id, file_type=file_type)
    await state.set_state(AddMovie.code)
    b = InlineKeyboardBuilder()
    b.button(text="🔢 Avtomatik kod", callback_data="add:auto")
    b.button(text="❌ Bekor qilish", callback_data="adm:cancel")
    b.adjust(1)
    await message.answer(
        "🔢 Kino uchun <b>kod</b> (raqam) yuboring. Masalan: <code>7</code>\n"
        "Yoki avtomatik kod olish uchun tugmani bosing.",
        reply_markup=b.as_markup(),
    )


@admin_router.message(AddMovie.video)
async def add_video_wrong(message: Message):
    await message.answer("❗ Iltimos, video yoki fayl yuboring.", reply_markup=cancel_kb())


@admin_router.callback_query(F.data == "add:auto", AddMovie.code)
async def add_auto(call: CallbackQuery, state: FSMContext):
    code = await next_code()
    await state.update_data(code=code)
    await state.set_state(AddMovie.title)
    await call.message.answer(
        f"✅ Kod: <b>{code}</b>\n\n✏️ Endi kino nomini yozing:", reply_markup=cancel_kb()
    )
    await call.answer()


@admin_router.message(AddMovie.code, F.text)
async def add_code(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 999999999):
        await message.answer("❗ Faqat raqam yuboring (masalan 7).", reply_markup=cancel_kb())
        return
    code = int(text)
    existing = await movie_title(code)
    if existing:
        await message.answer(
            f"⚠️ <b>{code}</b> kodi band: {html.escape(existing)}\nBoshqa raqam yuboring.",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(code=code)
    await state.set_state(AddMovie.title)
    await message.answer(
        f"✅ Kod: <b>{code}</b>\n\n✏️ Endi kino nomini yozing:", reply_markup=cancel_kb()
    )


@admin_router.message(AddMovie.title, F.text)
async def add_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if title.startswith("/"):
        await message.answer("❗ Kino nomini yozing yoki /cancel bosing.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    ok = await add_movie(data["code"], data["file_id"], data["file_type"], title[:200])
    await state.clear()
    if ok:
        await message.answer(
            f"✅ Kino qo'shildi!\n\n🎬 <b>{html.escape(title[:200])}</b>\n"
            f"🔢 Kodi: <b>{data['code']}</b>",
            reply_markup=admin_kb(),
        )
    else:
        await message.answer(
            "⚠️ Bu kod boshqa kinoga berilib bo'lgan. /add bilan qaytadan urinib ko'ring.",
            reply_markup=admin_kb(),
        )


# --- tezkor yuklash: video + izoh "5 Kino nomi" ---
@admin_router.message(StateFilter(None), F.video | F.document)
async def quick_upload(message: Message):
    m = CAPTION_RE.match(message.caption or "")
    if not m:
        await message.answer(
            "ℹ️ Kino qo'shish uchun:\n"
            "• videoga izoh yozing: <code>5 Kino nomi</code>\n"
            "• yoki /add buyrug'ini bosing."
        )
        return
    code, title = int(m.group(1)), m.group(2).strip()[:200]
    file_id, file_type = extract_file(message)
    if await add_movie(code, file_id, file_type, title):
        await message.answer(
            f"✅ Saqlandi!\n\n🎬 <b>{html.escape(title)}</b>\n🔢 Kodi: <b>{code}</b>"
        )
    else:
        existing = await movie_title(code)
        await message.answer(
            f"⚠️ <b>{code}</b> kodi band: {html.escape(existing or '')}\n"
            "Boshqa kod yozing yoki avval /del bilan o'chiring."
        )


# --- kino o'chirish ---
@admin_router.message(Command("del"))
async def del_cmd(message: Message, state: FSMContext, command: CommandObject):
    arg = (command.args or "").strip()
    if arg.isdigit():
        await state.clear()
        ok = await delete_movie(int(arg))
        await message.answer(
            "✅ O'chirildi." if ok else "❌ Bunday kodli kino topilmadi.", reply_markup=admin_kb()
        )
        return
    await state.set_state(DelMovie.code)
    await message.answer("🗑 O'chiriladigan kino kodini yuboring:", reply_markup=cancel_kb())


@admin_router.callback_query(F.data == "adm:del")
async def del_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(DelMovie.code)
    await call.message.answer("🗑 O'chiriladigan kino kodini yuboring:", reply_markup=cancel_kb())
    await call.answer()


@admin_router.message(DelMovie.code, F.text)
async def del_code(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❗ Faqat raqam yuboring yoki /cancel bosing.", reply_markup=cancel_kb())
        return
    ok = await delete_movie(int(text))
    await state.clear()
    await message.answer(
        "✅ O'chirildi." if ok else "❌ Bunday kodli kino topilmadi.", reply_markup=admin_kb()
    )


# --- ro'yxat ---
@admin_router.callback_query(F.data == "adm:list")
async def list_cb(call: CallbackQuery):
    rows = await list_movies()
    if not rows:
        await call.message.answer("📭 Hozircha kino yo'q.", reply_markup=admin_kb())
    else:
        chunk = ""
        for c, t, v in rows:
            line = f"<b>{c}</b> — {html.escape(t)} (👁 {v})\n"
            if len(chunk) + len(line) > 3800:
                await call.message.answer(chunk)
                chunk = ""
            chunk += line
        if chunk:
            await call.message.answer(chunk, reply_markup=admin_kb())
    await call.answer()


# --- statistika ---
@admin_router.callback_query(F.data == "adm:stats")
async def stats_cb(call: CallbackQuery):
    users, chats, movies, views = await get_stats()
    await call.message.answer(
        f"📊 <b>Statistika</b>\n\n👤 Foydalanuvchilar: {users}\n👥 Guruhlar: {chats}\n"
        f"🎬 Kinolar: {movies}\n👁 Jami ko'rishlar: {views}",
        reply_markup=admin_kb(),
    )
    await call.answer()


# --- xabar yuborish ---
@admin_router.callback_query(F.data == "adm:bc")
async def bc_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.msg)
    await call.message.answer(
        "📢 Hammaga yuboriladigan xabarni yuboring (matn, rasm, video...):",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@admin_router.message(Broadcast.msg)
async def bc_send(message: Message, state: FSMContext, bot: Bot):
    if message.text and message.text.startswith("/"):
        await message.answer("❗ Xabar yuboring yoki /cancel bosing.", reply_markup=cancel_kb())
        return
    await state.clear()
    ids = await all_user_ids()
    await message.answer(f"⏳ Yuborilmoqda... ({len(ids)} ta foydalanuvchi)")
    ok = fail = 0
    for uid in ids:
        try:
            await bot.copy_message(uid, message.chat.id, message.message_id)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(uid, message.chat.id, message.message_id)
                ok += 1
            except Exception:
                fail += 1
        except TelegramForbiddenError:
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await message.answer(
        f"✅ Tugadi.\n\nYetkazildi: {ok}\nYetmadi: {fail}", reply_markup=admin_kb()
    )


# ===================== FOYDALANUVCHI =====================
user_router = Router()
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


async def send_movie(message: Message, movie):
    code, file_id, file_type, title, views = movie
    caption = movie_caption(code, title, views + 1)
    if file_type == "video":
        await message.reply_video(video=file_id, caption=caption)
    else:
        await message.reply_document(document=file_id, caption=caption)


def group_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🤖 Botga o'tish", url=f"https://t.me/{BOT_USERNAME}")
    return b.as_markup()


# --- bot guruhga qo'shilganda ---
@user_router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def bot_joined(event: ChatMemberUpdated, bot: Bot):
    if event.chat.type not in GROUP_TYPES:
        return
    await add_chat(event.chat)
    try:
        await bot.send_message(event.chat.id, GROUP_TEXT, reply_markup=group_kb())
    except Exception as e:
        logging.warning("Guruhga salom yuborib bo'lmadi: %s", e)


# --- /start ---
@user_router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start_private(message: Message, bot: Bot):
    await add_user(message.from_user)
    admin = is_admin_id(message.from_user.id)
    if admin:
        await set_admin_commands(bot, message.from_user.id)
    if not await ensure_sub(message, bot):
        return
    await message.answer(start_text(message.from_user.full_name), reply_markup=start_kb(admin))


@user_router.message(CommandStart(), F.chat.type.in_(GROUP_TYPES))
async def start_group(message: Message):
    await add_chat(message.chat)
    await message.answer(GROUP_TEXT, reply_markup=group_kb())


@user_router.callback_query(F.data == "check_sub")
async def check_sub(call: CallbackQuery, bot: Bot):
    if await not_subscribed(bot, call.from_user.id):
        await call.answer("❌ Hali hamma kanalga obuna bo'lmadingiz!", show_alert=True)
        return
    await call.message.answer(
        start_text(call.from_user.full_name),
        reply_markup=start_kb(is_admin_id(call.from_user.id)),
    )
    await call.answer()


# --- tugmalar (start xabaridagi) ---
async def edit_or_pass(call: CallbackQuery, text: str, kb):
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await call.answer()


@user_router.callback_query(F.data == "u:back")
async def cb_back(call: CallbackQuery):
    await edit_or_pass(
        call, start_text(call.from_user.full_name), start_kb(is_admin_id(call.from_user.id))
    )


@user_router.callback_query(F.data == "u:help")
async def cb_help(call: CallbackQuery):
    text = HELP_TEXT + (ADMIN_HELP if is_admin_id(call.from_user.id) else "")
    await edit_or_pass(call, text, back_kb())


@user_router.callback_query(F.data == "u:about")
async def cb_about(call: CallbackQuery):
    await edit_or_pass(call, ABOUT_TEXT, back_kb())


@user_router.callback_query(F.data == "u:top")
async def cb_top(call: CallbackQuery):
    await edit_or_pass(call, await top_text(), back_kb())


@user_router.callback_query(F.data == "u:new")
async def cb_new(call: CallbackQuery):
    await edit_or_pass(call, await new_text(), back_kb())


# --- buyruqlar (guruhda ham, shaxsiyda ham) ---
@user_router.message(Command("help"))
async def cmd_help(message: Message):
    text = HELP_TEXT
    if message.chat.type == ChatType.PRIVATE and is_admin_id(message.from_user.id):
        text += ADMIN_HELP
    await message.answer(text)


@user_router.message(Command("about"))
async def cmd_about(message: Message):
    await message.answer(ABOUT_TEXT)


@user_router.message(Command("id"))
async def cmd_id(message: Message):
    text = f"🆔 Sizning ID: <code>{message.from_user.id}</code>"
    if message.chat.type != ChatType.PRIVATE:
        text += f"\n👥 Guruh ID: <code>{message.chat.id}</code>"
    await message.answer(text)


@user_router.message(Command("top"))
async def cmd_top(message: Message, bot: Bot):
    if await ensure_sub(message, bot):
        await message.answer(await top_text())


@user_router.message(Command("new"))
async def cmd_new(message: Message, bot: Bot):
    if await ensure_sub(message, bot):
        await message.answer(await new_text())


@user_router.message(Command("random"))
async def cmd_random(message: Message, bot: Bot):
    if not await ensure_sub(message, bot):
        return
    code = await random_code()
    if code is None:
        await message.answer("📭 Hozircha kino yo'q.")
        return
    movie = await get_movie(code)
    if movie:
        await send_movie(message, movie)


async def do_search(message: Message, query: str):
    rows = await search_movies(query)
    if not rows:
        await message.reply("❌ Bunday nomli kino topilmadi.\n🔢 Kino kodini yuborib ko'ring.")
        return
    await message.reply(
        f"🔎 <b>Natijalar:</b>\n\n{fmt_rows(rows)}\n\n🔢 Kino olish uchun kodini yuboring."
    )


@user_router.message(Command("search"))
async def cmd_search(message: Message, bot: Bot, command: CommandObject):
    if not await ensure_sub(message, bot):
        return
    query = (command.args or "").strip()
    if not query:
        await message.reply("🔎 Kino nomini yozing. Masalan: <code>/search Titanik</code>")
        return
    await do_search(message, query)


# --- kod bo'yicha kino ---
@user_router.message(F.text.regexp(r"^\d{1,9}$"))
async def by_code(message: Message, bot: Bot):
    if message.chat.type == ChatType.PRIVATE:
        await add_user(message.from_user)
        if not await ensure_sub(message, bot):
            return
    else:
        await add_chat(message.chat)

    movie = await get_movie(int(message.text))
    if not movie:
        await message.reply("❌ Bunday kodli kino topilmadi.")
        return
    await send_movie(message, movie)


# --- shaxsiy chatda oddiy matn = nom bo'yicha qidiruv ---
@user_router.message(F.chat.type == ChatType.PRIVATE, F.text)
async def other_text(message: Message, bot: Bot):
    if message.text.startswith("/"):
        await message.answer("❓ Noma'lum buyruq. /help ni bosing.")
        return
    if not await ensure_sub(message, bot):
        return
    await do_search(message, message.text.strip()[:100])


# ===================== ISHGA TUSHIRISH =====================
async def main():
    global BOT_USERNAME
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    await setup_commands(bot)
    dp = Dispatcher()
    dp.include_router(admin_router)
    dp.include_router(user_router)
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi: @%s", BOT_USERNAME)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())