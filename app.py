import os
import sys
import asyncio
import nest_asyncio
import sqlite3
import random
import string
from typing import List, Tuple, Optional
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest

# ==================== فعال‌سازی nest_asyncio ====================
nest_asyncio.apply()

# ==================== تنظیمات اصلی ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8698848639:AAGywJf9fu0LrvPByHGDtPTkHm5hg4JMsDs")
OWNER_ID = 1616545329

CHANNELS = [
    {
        "chat_id": -1003513747430,
        "link": "https://t.me/+XG_zo4wJLl5kYzg0",
        "name": "NightClub"
    }
]

# ==================== دیتابیس (مسیر مطلق در Render) ====================
# در Render می‌توانیم از /tmp استفاده کنیم (موقتی) یا در دایرکتوری خود پروژه.
# برای پایداری بیشتر، مسیر را در دایرکتوری فعلی می‌گذاریم.
DB_PATH = os.path.join(os.path.dirname(__file__), "files.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_param TEXT UNIQUE NOT NULL,
    caption TEXT,
    views_count INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS set_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    order_num INTEGER NOT NULL,
    FOREIGN KEY(set_id) REFERENCES sets(id) ON DELETE CASCADE
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS set_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    set_param TEXT NOT NULL,
    UNIQUE(user_id, set_param)
)
""")
db.commit()

# ==================== توابع کمکی (بدون تغییر) ====================
def generate_unique_param(length=8) -> str:
    chars = string.ascii_letters + string.digits
    while True:
        param = ''.join(random.choices(chars, k=length))
        cursor.execute("SELECT start_param FROM sets WHERE start_param = ?", (param,))
        if not cursor.fetchone():
            return param

def create_set(caption: str = "") -> str:
    param = generate_unique_param()
    cursor.execute("INSERT INTO sets (start_param, caption) VALUES (?, ?)", (param, caption))
    db.commit()
    return param

def add_file_to_set(set_id: int, file_id: str, file_type: str, order: int):
    cursor.execute(
        "INSERT INTO set_files (set_id, file_id, file_type, order_num) VALUES (?, ?, ?, ?)",
        (set_id, file_id, file_type, order)
    )
    db.commit()

def get_set_by_param(param: str) -> Optional[Tuple[int, str, int]]:
    cursor.execute("SELECT id, caption, views_count FROM sets WHERE start_param = ?", (param,))
    return cursor.fetchone()

def get_set_files(set_id: int) -> List[Tuple[str, str]]:
    cursor.execute("SELECT file_id, file_type FROM set_files WHERE set_id = ? ORDER BY order_num", (set_id,))
    return cursor.fetchall()

def increment_view_unique(user_id: int, set_param: str) -> bool:
    try:
        cursor.execute("INSERT INTO set_views (user_id, set_param) VALUES (?, ?)", (user_id, set_param))
        db.commit()
        cursor.execute("UPDATE sets SET views_count = views_count + 1 WHERE start_param = ?", (set_param,))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def get_view_count(set_param: str) -> int:
    cursor.execute("SELECT views_count FROM sets WHERE start_param = ?", (set_param,))
    result = cursor.fetchone()
    return result[0] if result else 0

# ==================== بررسی عضویت ====================
async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> Tuple[bool, List[str]]:
    not_member = []
    for ch in CHANNELS:
        chat_id = ch.get("chat_id") or f"@{ch['username']}"
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                not_member.append(ch.get("name", ch.get("username", "کانال")))
        except Exception as e:
            print(f"⚠️ خطا در بررسی {ch.get('name', ch.get('username', 'کانال'))}: {e}")
            not_member.append(ch.get("name", ch.get("username", "کانال")))
    return (len(not_member) == 0, not_member)

def get_join_buttons(not_member_channels: List[str]) -> InlineKeyboardMarkup:
    buttons = []
    for ch in CHANNELS:
        name = ch.get("name", ch.get("username", "کانال"))
        if name in not_member_channels:
            buttons.append([InlineKeyboardButton(f"📢 عضویت در {name}", url=ch["link"])])
    buttons.append([InlineKeyboardButton("✅ بررسی مجدد", callback_data="check_again")])
    return InlineKeyboardMarkup(buttons)

# ==================== ارسال فایل ====================
async def send_file(target, file_id: str, file_type: str, caption: str = ""):
    if file_type == "photo":
        return await target.reply_photo(photo=file_id, caption=caption)
    elif file_type == "video":
        return await target.reply_video(video=file_id, caption=caption)
    elif file_type == "audio":
        return await target.reply_audio(audio=file_id, caption=caption)
    else:
        return await target.reply_document(document=file_id, caption=caption)

async def delete_messages_after_delay(bot, chat_id: int, message_ids: List[int], delay: int = 30):
    await asyncio.sleep(delay)
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            print(f"⚠️ خطا در حذف پیام {msg_id}: {e}")

async def send_set_with_warning(target, set_param: str, context: ContextTypes.DEFAULT_TYPE):
    set_info = get_set_by_param(set_param)
    if not set_info:
        await target.reply_text("❌ مجموعه یافت نشد.")
        return
    set_id, caption, views = set_info
    files = get_set_files(set_id)
    if not files:
        await target.reply_text("❌ این مجموعه خالی است.")
        return

    user_id = target.chat.id
    increment_view_unique(user_id, set_param)
    new_views = get_view_count(set_param)
    final_caption = f"{caption}\n\n👁 بازدید: {new_views}" if caption else f"👁 بازدید: {new_views}"

    warning_text = (
        "⚠️ این مجموعه فقط به مدت ۳۰ ثانیه در دسترس است.\n"
        "لطفاً سریعاً هر فایل را به **سیو مسیج خود** ارسال کنید (روی هر فایل کلیک کنید و Forward را بزنید)."
    )
    warning_msg = await target.reply_text(warning_text)

    sent_messages = []
    for file_id, file_type in files:
        msg = await send_file(target, file_id, file_type, final_caption if len(files) == 1 else "")
        sent_messages.append(msg.message_id)

    chat_id = target.chat.id
    message_ids = [warning_msg.message_id] + sent_messages
    asyncio.create_task(delete_messages_after_delay(context.bot, chat_id, message_ids, delay=30))

# ==================== هندلرها (همانند فایل اصلی) ====================
async def new_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    context.user_data['temp_set'] = []
    await update.message.reply_text(
        "🆕 ساخت مجموعه جدید آغاز شد.\n"
        "لطفاً فایل‌های مورد نظر را یکی یکی ارسال کنید.\n"
        "پس از اتمام، دستور /save_set را بفرستید.\n"
        "برای لغو: /cancel_set"
    )

async def save_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    temp_set = context.user_data.get('temp_set', [])
    if not temp_set:
        await update.message.reply_text("❌ هیچ فایلی برای ذخیره یافت نشد. ابتدا فایل ارسال کنید.")
        return
    context.user_data['pending_set'] = temp_set.copy()
    await update.message.reply_text("✏️ لطفاً یک کپشن برای این مجموعه وارد کنید (یا /skip برای رد کردن):")
    context.user_data['awaiting_caption'] = True

async def cancel_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    context.user_data.pop('temp_set', None)
    context.user_data.pop('pending_set', None)
    context.user_data.pop('awaiting_caption', None)
    await update.message.reply_text("❌ ساخت مجموعه لغو شد.")

async def handle_admin_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if context.user_data.get('awaiting_caption'):
        await update.message.reply_text("لطفاً ابتدا کپشن مجموعه را وارد کنید یا /skip بزنید.")
        return
    msg = update.message
    file_id = None
    file_type = None
    if msg.document:
        file_id = msg.document.file_id
        file_type = "document"
    elif msg.video:
        file_id = msg.video.file_id
        file_type = "video"
    elif msg.audio:
        file_id = msg.audio.file_id
        file_type = "audio"
    elif msg.photo:
        file_id = msg.photo[-1].file_id
        file_type = "photo"
    if file_id and file_type:
        temp_set = context.user_data.get('temp_set', [])
        temp_set.append((file_id, file_type))
        context.user_data['temp_set'] = temp_set
        await msg.reply_text(f"✅ فایل شماره {len(temp_set)} ذخیره شد. می‌توانید ادامه دهید.")
    else:
        await msg.reply_text("❌ نوع فایل پشتیبانی نمی‌شود.")

async def handle_caption_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_caption'):
        return
    text = update.message.text
    caption = "" if text == "/skip" else text
    context.user_data.pop('awaiting_caption')
    temp_set = context.user_data.pop('pending_set', [])
    if not temp_set:
        await update.message.reply_text("❌ خطا: مجموعه یافت نشد.")
        return
    set_param = create_set(caption)
    set_id = get_set_by_param(set_param)[0]
    for idx, (file_id, file_type) in enumerate(temp_set, start=1):
        add_file_to_set(set_id, file_id, file_type, idx)
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={set_param}"
    await update.message.reply_text(
        f"✅ مجموعه با {len(temp_set)} فایل ذخیره شد.\n\n🔗 لینک اختصاصی:\n{link}",
        disable_web_page_preview=True
    )
    context.user_data.pop('temp_set', None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("👋 به ربات خوش آمدید!")
        return
    param = context.args[0]
    set_info = get_set_by_param(param)
    if not set_info:
        await update.message.reply_text("❌ لینک نامعتبر است.")
        return
    user_id = update.effective_user.id
    is_member, not_member = await check_membership(user_id, context)
    if is_member:
        await send_set_with_warning(update.message, param, context)
    else:
        context.user_data['pending_set_param'] = param
        keyboard = get_join_buttons(not_member)
        await update.message.reply_text(
            "❌ برای دریافت این مجموعه باید در کانال‌های زیر عضو شوید:",
            reply_markup=keyboard
        )

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    param = context.user_data.get('pending_set_param')
    if not param:
        await query.edit_message_text("❌ اطلاعات مجموعه یافت نشد.")
        return
    is_member, not_member = await check_membership(user_id, context)
    if is_member:
        await send_set_with_warning(query.message, param, context)
        await query.message.delete()
        context.user_data.pop('pending_set_param', None)
    else:
        new_keyboard = get_join_buttons(not_member)
        try:
            await query.edit_message_text(
                "❌ همچنان عضو نیستید. لطفاً ابتدا عضو شوید:",
                reply_markup=new_keyboard
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise e

# ==================== ساخت Application و اضافه کردن هندلرها ====================
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("new_set", new_set))
application.add_handler(CommandHandler("save_set", save_set))
application.add_handler(CommandHandler("cancel_set", cancel_set))
application.add_handler(MessageHandler(filters.TEXT & filters.User(user_id=OWNER_ID), handle_caption_input))
application.add_handler(MessageHandler(
    filters.ChatType.PRIVATE & filters.User(user_id=OWNER_ID) &
    (filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO),
    handle_admin_file
))
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="^check_again$"))

# مقداردهی اولیه (اجباری برای نسخه 20+)
try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        asyncio.ensure_future(application.initialize())
    else:
        loop.run_until_complete(application.initialize())
except RuntimeError:
    asyncio.run(application.initialize())

# ==================== راه‌اندازی Flask ====================
app = Flask(__name__)

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)
    asyncio.run(application.process_update(update))
    return 'OK', 200

@app.route('/')
def index():
    return "ربات چند فایلی با موفقیت روی Render فعال است!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
