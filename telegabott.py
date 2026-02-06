import os
import json
import asyncio
import shutil
from collections import defaultdict
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile
)
from aiogram.filters import CommandStart

import discord

# ───────── ENV ─────────
load_dotenv("key.env")

TG_TOKEN = os.getenv("TG_TOKEN")
DC_TOKEN = os.getenv("DC_TOKEN")
# Добавим проверку на существование переменных
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID") or 0)
DEFAULT_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID") or 0)

# ───────── PATHS ─────────
TMP_DIR = "tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# ───────── STATE ─────────
STATE_FILE = "state.json"
state = {
    "enabled": True,
    "tg_chat_id": None,
    "discord_channel_id": DEFAULT_CHANNEL_ID,
    "reply_map": {}
}

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            state.update(json.load(f))
        except json.JSONDecodeError:
            pass

def save_state():
    # Ограничиваем размер reply_map, чтобы файл не весил мегабайты
    if len(state["reply_map"]) > 400: # 200 пар ID
        keys = list(state["reply_map"].keys())
        for k in keys[:200]:
            state["reply_map"].pop(k, None)
            
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# ───────── TELEGRAM ─────────
bot = Bot(TG_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

def main_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🟢 Включить", callback_data="on"),
                InlineKeyboardButton(text="🔴 Выключить", callback_data="off"),
            ],
            [
                InlineKeyboardButton(text="🔁 Сменить Discord-канал", callback_data="set_channel"),
            ],
            [
                InlineKeyboardButton(text="📡 Статус", callback_data="status"),
            ]
        ]
    )

@router.message(CommandStart())
async def start(msg: Message):
    state["tg_chat_id"] = msg.chat.id
    save_state()
    await msg.answer("🧠 Панель управления мостом", reply_markup=main_kb())

# Исправленные обработчики кнопок
@router.callback_query(F.data == "on")
async def on_cb(call: CallbackQuery):
    state["enabled"] = True
    save_state()
    await call.message.edit_text("🟢 Мост включен", reply_markup=main_kb())
    await call.answer() # Убирает "часики"

@router.callback_query(F.data == "off")
async def off_cb(call: CallbackQuery):
    state["enabled"] = False
    save_state()
    await call.message.edit_text("🔴 Мост выключен", reply_markup=main_kb())
    await call.answer()

@router.callback_query(F.data == "status")
async def status_cb(call: CallbackQuery):
    status_text = "🟢 Онлайн" if state["enabled"] else "🔴 Выключен"
    await call.answer(
        f"{status_text}\nКанал: {state['discord_channel_id']}",
        show_alert=True
    )

@router.callback_query(F.data == "set_channel")
async def set_ch(call: CallbackQuery):
    await call.message.answer("✏️ Отправь ID Discord-канала (17-20 цифр)")
    await call.answer()

@router.message(F.text.regexp(r"^\d{17,20}$"))
async def set_channel_id(msg: Message):
    if msg.chat.id != state["tg_chat_id"]:
        return
    state["discord_channel_id"] = int(msg.text)
    save_state()
    await msg.answer(f"✅ Канал обновлён: {msg.text}")

# ───────── TG → DC ─────────
@router.message(F.photo | F.document | F.text)
async def tg_to_dc(msg: Message):
    if not state["enabled"] or msg.chat.id != state["tg_chat_id"] or (msg.text and msg.text.startswith("/")):
        return

    guild = dc.get_guild(GUILD_ID)
    if not guild: return
    channel = guild.get_channel(state["discord_channel_id"])
    if not channel: return

    reply_to = state["reply_map"].get(str(msg.reply_to_message.message_id)) if msg.reply_to_message else None
    files = []

    if msg.photo:
        file_info = await bot.get_file(msg.photo[-1].file_id)
        path = f"{TMP_DIR}/{file_info.file_id}.jpg"
        await bot.download_file(file_info.file_path, path)
        files.append(discord.File(path))

    if msg.document:
        file_info = await bot.get_file(msg.document.file_id)
        path = f"{TMP_DIR}/{msg.document.file_name}"
        await bot.download_file(file_info.file_path, path)
        files.append(discord.File(path))

    content = msg.text or msg.caption or ""
    
    sent = await channel.send(
        content=f"**[TG | {msg.from_user.username or msg.from_user.id}]**\n{content}",
        files=files if files else None,
        reference=discord.MessageReference(
            message_id=int(reply_to),
            channel_id=channel.id
        ) if reply_to else None
    )

    state["reply_map"][str(msg.message_id)] = str(sent.id)
    state["reply_map"][str(sent.id)] = str(msg.message_id)
    save_state()

# ───────── DISCORD ─────────
intents = discord.Intents.default()
intents.message_content = True
dc = discord.Client(intents=intents)

@dc.event
async def on_ready():
    print(f"🟢 Discord READY: {dc.user}")

@dc.event
async def on_message(message: discord.Message):
    if message.author.bot or not state["enabled"] or message.channel.id != state["discord_channel_id"] or not state["tg_chat_id"]:
        return

    reply_to = state["reply_map"].get(str(message.reference.message_id)) if message.reference else None
    
    header = f"<b>[DC | {message.author.name}]:</b>"
    
    if message.attachments:
        att = message.attachments[0]
        path = f"{TMP_DIR}/{att.filename}"
        await att.save(path)
        
        sent = await bot.send_document(
            chat_id=state["tg_chat_id"],
            document=FSInputFile(path),
            caption=f"{header}\n{message.content}",
            reply_to_message_id=int(reply_to) if reply_to else None,
            parse_mode="HTML"
        )
    else:
        sent = await bot.send_message(
            chat_id=state["tg_chat_id"],
            text=f"{header}\n{message.content}",
            reply_to_message_id=int(reply_to) if reply_to else None,
            parse_mode="HTML"
        )

    state["reply_map"][str(message.id)] = str(sent.message_id)
    state["reply_map"][str(sent.message_id)] = str(message.id)
    save_state()

# ───────── MAIN ─────────
async def main():
    # Чистим tmp только при запуске
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    
    # Запуск без gather для стабильности в Pydroid
    asyncio.create_task(dc.start(DC_TOKEN))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
