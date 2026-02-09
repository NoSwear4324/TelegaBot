import os
import json
import asyncio
import shutil
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart

import discord
from discord import Webhook

# ───────── CONFIG ─────────
load_dotenv("key.env")
TG_TOKEN = os.getenv("TG_TOKEN")
DC_TOKEN = os.getenv("DC_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID") or 0)
DEFAULT_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID") or 0)

TMP_DIR = "tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# ───────── STATE ─────────
STATE_FILE = "state.json"
state = {"enabled": True, "dnd": False, "tg_chat_id": None, "discord_channel_id": DEFAULT_CHANNEL_ID, "reply_map": {}}

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try: state.update(json.load(f))
        except: pass

def save_state():
    if len(state["reply_map"]) > 2000:
        keys = list(state["reply_map"].keys())
        for k in keys[:1000]: state["reply_map"].pop(k, None)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

# ───────── TELEGRAM ─────────
bot = Bot(TG_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

def main_kb():
    dnd_status = "💤 DND: ON" if state.get("dnd") else "🔔 DND: OFF"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 ON", callback_data="on"), 
         InlineKeyboardButton(text="🔴 OFF", callback_data="off")],
        [InlineKeyboardButton(text=dnd_status, callback_data="toggle_dnd")],
        [InlineKeyboardButton(text="🔁 Канал", callback_data="set_channel"), 
         InlineKeyboardButton(text="📡 Статус", callback_data="status")]
    ])

@router.message(CommandStart())
async def start(msg: Message):
    state["tg_chat_id"] = msg.chat.id
    save_state()
    await msg.answer("🚀 Мост TG ↔ DC запущен!\nСтикеры починены.", reply_markup=main_kb())

@router.callback_query(F.data == "toggle_dnd")
async def toggle_dnd(call: CallbackQuery):
    state["dnd"] = not state.get("dnd", False)
    save_state()
    try: await call.message.edit_reply_markup(reply_markup=main_kb())
    except: pass
    await call.answer(f"DND: {'ВКЛ' if state['dnd'] else 'ВЫКЛ'}")

@router.callback_query(F.data.in_(["on", "off"]))
async def toggle(call: CallbackQuery):
    state["enabled"] = (call.data == "on")
    save_state()
    try: await call.message.edit_text(f"Мост: {'🟢 ВКЛ' if state['enabled'] else '🔴 ВЫКЛ'}", reply_markup=main_kb())
    except: pass
    await call.answer()

@router.callback_query(F.data == "status")
async def status_check(call: CallbackQuery):
    status = "🟢 Онлайн" if state["enabled"] else "🔴 Оффлайн"
    await call.answer(f"{status}\nDND: {state.get('dnd')}\nКанал: {state['discord_channel_id']}", show_alert=True)

@router.callback_query(F.data == "set_channel")
async def set_channel_req(call: CallbackQuery):
    await call.message.answer("✏️ Отправь ID канала Discord")
    await call.answer()

@router.message(F.text.regexp(r"^\d{17,20}$"))
async def update_channel(msg: Message):
    if msg.chat.id != state["tg_chat_id"]: return
    state["discord_channel_id"] = int(msg.text)
    save_state()
    await msg.answer(f"✅ Канал изменен на: {msg.text}")

# ───────── TG → DC ─────────
async def get_webhook(channel):
    try:
        webhooks = await channel.webhooks()
        webhook = next((wh for wh in webhooks if wh.name == "Bridge"), None)
        return webhook or await channel.create_webhook(name="Bridge")
    except: return None

@router.message()
async def tg_to_dc(msg: Message):
    if not state["enabled"] or msg.chat.id != state["tg_chat_id"] or (msg.text and msg.text.startswith("/")):
        return

    try:
        guild = dc.get_guild(GUILD_ID)
        channel = guild.get_channel(state["discord_channel_id"])
        if not channel: return
        webhook = await get_webhook(channel)
        if not webhook: return

        path = None
        file_to_send = None
        
        # Фикс стикеров: берем любой стикер, кроме анимированных
        if msg.sticker:
            if msg.sticker.is_animated or msg.sticker.is_video:
                # Анимированные пока пропускаем, чтобы не упасть
                pass 
            file_info = await bot.get_file(msg.sticker.file_id)
            path = f"{TMP_DIR}/st_{msg.sticker.file_id}.webp"
            await bot.download_file(file_info.file_path, path)
            file_to_send = discord.File(path, filename="sticker.png") # Discord поймет webp как png
        else:
            media = msg.photo[-1] if msg.photo else (msg.document or msg.video or msg.audio or msg.voice)
            if media:
                file_info = await bot.get_file(media.file_id)
                ext = file_info.file_path.split('.')[-1]
                path = f"{TMP_DIR}/f_{media.file_id}.{ext}"
                await bot.download_file(file_info.file_path, path)
                file_to_send = discord.File(path)

        content = msg.text or msg.caption or ""
        if msg.reply_to_message:
            reply_id = state["reply_map"].get(str(msg.reply_to_message.message_id))
            if reply_id:
                link = f"https://discord.com/channels/{GUILD_ID}/{channel.id}/{reply_id}"
                content = f"⤴️ **[В ответ на сообщение]({link})**\n{content}"

        # Отправка
        payload = {
            "content": content[:2000] if (content or file_to_send) else "...",
            "username": (msg.from_user.full_name or "User")[:32],
            "wait": True
        }
        if file_to_send: payload["file"] = file_to_send
        
        try:
            ups = await bot.get_user_profile_photos(msg.from_user.id, limit=1)
            if ups.total_count > 0:
                img = await bot.get_file(ups.photos[0][-1].file_id)
                payload["avatar_url"] = f"https://api.telegram.org/file/bot{TG_TOKEN}/{img.file_path}"
        except: pass

        sent = await webhook.send(**payload)
        state["reply_map"][str(msg.message_id)] = str(sent.id)
        state["reply_map"][str(sent.id)] = str(msg.message_id)
        save_state()

        if path and os.path.exists(path): os.remove(path)
    except Exception as e: print(f"❌ TG->DC: {e}")

# ───────── DC → TG ─────────
intents = discord.Intents.default()
intents.message_content = True
dc = discord.Client(intents=intents)

@dc.event
async def on_ready(): print(f"🟢 Discord онлайн: {dc.user}")

@dc.event
async def on_message(message: discord.Message):
    if message.author.bot or message.webhook_id or not state["enabled"] or state.get("dnd"): return
    if message.channel.id != state["discord_channel_id"]: return

    try:
        reply_id = state["reply_map"].get(str(message.reference.message_id)) if message.reference else None
        header = f"<b>[DC | {message.author.display_name}]:</b>"
        
        # 1. Сначала проверяем файлы (картинки, документы)
        if message.attachments:
            for att in message.attachments:
                path = f"{TMP_DIR}/{att.filename}"
                await att.save(path)
                sent = await bot.send_document(
                    state["tg_chat_id"], FSInputFile(path), 
                    caption=f"{header}\n{message.content or ''}" if att == message.attachments[0] else None, 
                    reply_to_message_id=int(reply_id) if reply_id else None, parse_mode="HTML"
                )
                os.remove(path)
                # Сохраняем ID для реплаев
                state["reply_map"][str(message.id)] = str(sent.message_id)
                state["reply_map"][str(sent.message_id)] = str(message.id)

        # 2. ВОТ СЮДА ВСТАВЛЯЕМ ПРОВЕРКУ СТИКЕРОВ
        elif message.stickers:
            for sticker in message.stickers:
                # Просто кидаем ссылку, ТГ сам сделает превью
                sent = await bot.send_message(
                    state["tg_chat_id"], 
                    f"{header}\nСтикер: {sticker.url}",
                    reply_to_message_id=int(reply_id) if reply_id else None,
                    parse_mode="HTML"
                )
                state["reply_map"][str(message.id)] = str(sent.message_id)
                state["reply_map"][str(sent.message_id)] = str(message.id)

        # 3. Если нет ни файлов, ни стикеров — шлем просто текст
        elif message.content:
            sent = await bot.send_message(
                state["tg_chat_id"], f"{header}\n{message.content}", 
                reply_to_message_id=int(reply_id) if reply_id else None, parse_mode="HTML"
            )
            state["reply_map"][str(message.id)] = str(sent.message_id)
            state["reply_map"][str(sent.message_id)] = str(message.id)

        save_state()
    except Exception as e: 
        print(f"❌ DC->TG: {e}")

async def main():
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    asyncio.create_task(dc.start(DC_TOKEN))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
