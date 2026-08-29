import asyncio
import os
import re
import time

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telethon import TelegramClient

from checker import UsernamePool, Worker, Status

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKENS = [t.strip() for t in os.environ["BOT_TOKENS"].split(",") if t.strip()]

MAX_PER_BATCH = 500
EDIT_INTERVAL = 2.0  # detik, jaga2 supaya edit progress sendiri ga kena rate limit

pool: UsernamePool | None = None


# ===================== POOL SETUP =====================
# Token yang sama dipakai dua fungsi sekaligus:
# - lewat Bot API (python-telegram-bot) buat nerima/reply pesan user
# - lewat MTProto (Telethon, login bot) buat panggil account.checkUsername
# Dua jalur ini beda transport, jadi aman jalan bareng tanpa bentrok.

async def build_pool() -> UsernamePool:
    os.makedirs("sessions", exist_ok=True)
    workers = []
    for i, token in enumerate(BOT_TOKENS):
        client = TelegramClient(f"sessions/checker_{i}", API_ID, API_HASH)
        await client.start(bot_token=token)
        workers.append(Worker(client=client, name=f"worker_{i}"))
    return UsernamePool(workers, min_delay=1.3)


# ===================== GENERATOR (persis dari versi lama) =====================

ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def gen_sop(word):
    result = []
    for i, c in enumerate(word):
        new = word[:i] + c + word[i:]
        if new != word:
            result.append(new)
    return list(dict.fromkeys(result))


def gen_tamhur(word):
    result = []
    for i in range(len(word) + 1):
        for c in ALPHABET:
            new = word[:i] + c + word[i:]
            if new != word:
                result.append(new)
    return list(dict.fromkeys(result))


def gen_gahur(word):
    result = []
    for i, orig in enumerate(word):
        for c in ALPHABET:
            if c != orig:
                new = word[:i] + c + word[i + 1:]
                result.append(new)
    return list(dict.fromkeys(result))


def gen_tamping(word):
    result = []
    for c in ALPHABET:
        result.append(c + word)
        result.append(word + c)
    return list(dict.fromkeys(result))


def gen_swap(word):
    result = []
    for i in range(len(word) - 1):
        lst = list(word)
        lst[i], lst[i + 1] = lst[i + 1], lst[i]
        new = "".join(lst)
        if new != word:
            result.append(new)
    return list(dict.fromkeys(result))


def format_list(usernames, per_line=8):
    lines = [
        " ".join(f"@{u}" for u in usernames[i:i + per_line])
        for i in range(0, len(usernames), per_line)
    ]
    return "```\n" + "\n".join(lines) + "\n```"


def split_message(text, limit=3800):
    lines = text.split("\n")
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    return chunks


def build_list_messages(label, usernames, per_line=8, limit=3500):
    """Bungkus satu grup hasil (mis. Available) jadi 1+ pesan code-block,
    tiap pesan sudah lengkap open+close backtick-nya sendiri (aman dipecah)."""
    if not usernames:
        return [f"*{label} (0):* -"]

    lines = [
        " ".join(f"@{u}" for u in usernames[i:i + per_line])
        for i in range(0, len(usernames), per_line)
    ]

    messages = []
    header = f"*{label} ({len(usernames)}):*"
    current_lines, current_len = [], len(header) + 10

    for line in lines:
        if current_len + len(line) + 1 > limit and current_lines:
            messages.append(header + "\n```\n" + "\n".join(current_lines) + "\n```")
            header = f"*{label} (lanjutan):*"
            current_lines, current_len = [], len(header) + 10
        current_lines.append(line)
        current_len += len(line) + 1

    if current_lines:
        messages.append(header + "\n```\n" + "\n".join(current_lines) + "\n```")

    return messages


async def cmd_sop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/sop fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    for chunk in split_message(format_list(gen_sop(word))):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_tamhur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/tamhur fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    for chunk in split_message(format_list(gen_tamhur(word))):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_gahur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/gahur fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    for chunk in split_message(format_list(gen_gahur(word))):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_tamping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/tamping fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    for chunk in split_message(format_list(gen_tamping(word))):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_swap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/swap fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    for chunk in split_message(format_list(gen_swap(word))):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Contoh: `/gen fikar`", parse_mode="Markdown")
        return
    word = context.args[0].lower().lstrip("@")
    all_results = list(dict.fromkeys(gen_sop(word) + gen_tamhur(word) + gen_tamping(word)))
    for chunk in split_message(format_list(all_results)):
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Kirim daftar username (pakai @, dipisah spasi/baris, sampai "
        f"{MAX_PER_BATCH} sekaligus) untuk dicek statusnya.\n\n"
        "Generator: /sop /tamhur /gahur /tamping /swap /gen <kata>\n"
        "/help untuk detail lengkap."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Cara Penggunaan Bot*\n\n"
        f"Kirim `@user1 @user2 ...` (boleh banyak baris, sampai {MAX_PER_BATCH} "
        "username sekaligus) untuk dicek.\n\n"
        "Hasil dikelompokkan:\n"
        "• *Available* — bisa langsung diklaim\n"
        "• *Frag & Banned* — dilelang Fragment atau diduga banned\n"
        "• *Taken* — cuma jumlahnya\n\n"
        "*Generator Username:*\n"
        "`/sop kata` `/tamhur kata` `/gahur kata` `/tamping kata` `/swap kata` `/gen kata`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ===================== CEK USERNAME (bagian baru) =====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    raw = re.findall(r"@?([a-zA-Z][a-zA-Z0-9_]{4,31})", text)

    seen = set()
    usernames = []
    for u in raw:
        key = u.lower()
        if key not in seen:
            seen.add(key)
            usernames.append(u)

    if not usernames:
        await update.message.reply_text(
            "⚠️ Tidak ada username valid yang ditemukan.\nContoh: `@fikar @gemini @grok`",
            parse_mode="Markdown",
        )
        return

    usernames = usernames[:MAX_PER_BATCH]
    total = len(usernames)

    status_msg = await update.message.reply_text(f"🔍 Mengecek {total} username... 0/{total}")

    done_count = 0
    lock = asyncio.Lock()
    last_edit = time.time()

    async def worker_task(username):
        nonlocal done_count, last_edit
        status = await pool.check(username)
        async with lock:
            done_count += 1
            now = time.time()
            if now - last_edit > EDIT_INTERVAL or done_count == total:
                last_edit = now
                try:
                    await status_msg.edit_text(f"🔍 Mengecek {total} username... {done_count}/{total}")
                except Exception:
                    pass  # kalau edit kena rate limit, abaikan aja, ga fatal
        return username, status

    results = await asyncio.gather(*(worker_task(u) for u in usernames))

    available = [u for u, s in results if s == Status.AVAILABLE]
    frag_banned = [u for u, s in results if s in (Status.FRAGMENT, Status.BANNED)]
    taken_count = sum(1 for _, s in results if s == Status.TAKEN)
    invalid_count = sum(1 for _, s in results if s == Status.INVALID)

    header = f"✅ Selesai cek {total} username\nTaken: {taken_count}"
    if invalid_count:
        header += f" | Invalid: {invalid_count}"

    messages = [header]
    messages += build_list_messages("Available", available)
    messages += build_list_messages("Frag & Banned", frag_banned)

    for msg in messages:
        await update.message.reply_text(msg, parse_mode="Markdown")


# ===================== RUN =====================

async def main():
    global pool
    pool = await build_pool()

    commands = [
        BotCommand("start", "Mulai"),
        BotCommand("help", "Cara pakai"),
        BotCommand("sop", "Bentukan semi on point"),
        BotCommand("tamhur", "Tambah huruf di semua posisi"),
        BotCommand("gahur", "Ganti huruf dengan a-z"),
        BotCommand("tamping", "Tambah huruf di kiri/kanan"),
        BotCommand("swap", "Tukar huruf berdekatan"),
        BotCommand("gen", "Semua bentukan sekaligus"),
    ]

    apps = []
    for token in BOT_TOKENS:
        app = ApplicationBuilder().token(token).build()
        await app.bot.set_my_commands(commands)
        app.add_handler(CommandHandler("start", cmd_start))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("sop", cmd_sop))
        app.add_handler(CommandHandler("tamhur", cmd_tamhur))
        app.add_handler(CommandHandler("gahur", cmd_gahur))
        app.add_handler(CommandHandler("tamping", cmd_tamping))
        app.add_handler(CommandHandler("swap", cmd_swap))
        app.add_handler(CommandHandler("gen", cmd_gen))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        apps.append(app)

    print(f"{len(apps)} bot jalan, pool checker siap ({len(BOT_TOKENS)} worker).")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
