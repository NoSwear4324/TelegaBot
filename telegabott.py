import os
import json
import asyncio
import shutil
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart
import discord
from discord import Webhook, File

# ───────── CONFIG ─────────
load_dotenv("key.env")
TG_TOKEN = os.getenv("TG_TOKEN")
DC_TOKEN = os.getenv("DC_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID") or 0)
DEFAULT_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID") or 0)
TMP_DIR = "tmp"
MAX_FILE_SIZE = 8 * 1024 * 1024  # ~8 MB

os.makedirs(TMP_DIR, exist_ok=True)

# ───────── STATE ─────────
STATE_FILE = "state.json"
state = {
    "enabled": True,
    "dnd": False,
    "tg_chat_id": None,
    "discord_channel_id": DEFAULT_CHANNEL_ID,
    "reply_map": {}
}

if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            state.update(json.load(f))
        except:
            pass

def save_state():
    if len(state["reply_map"]) > 3000:
        keys = list(state["reply_map"].keys())
        for k in keys[:1500]:
            state["reply_map"].pop(k, None)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ───────── TELEGRAM ─────────
bot = Bot(TG_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

def main_kb():
    dnd_status = "💤 DND: ON" if state.get("dnd") else "🔔 DND: OFF"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 ON", callback_data="on"),
            InlineKeyboardButton(text="🔴 OFF", callback_data="off")
        ],
        [InlineKeyboardButton(text=dnd_status, callback_data="toggle_dnd")],
        [
            InlineKeyboardButton(text="🔁 Канал", callback_data="set_channel"),
            InlineKeyboardButton(text="📡 Статус", callback_data="status")
        ]
    ])

@router.message(CommandStart())
async def start(msg: Message):
    state["tg_chat_id"] = msg.chat.id
    save_state()
    await msg.answer("🚀 Мост TG ↔ DC работает\nedit/delete + чистые стикеры", reply_markup=main_kb())

@router.callback_query(F.data == "toggle_dnd")
async def toggle_dnd(call: CallbackQuery):
    state["dnd"] = not state.get("dnd", False)
    save_state()
    try:
        await call.message.edit_reply_markup(reply_markup=main_kb())
    except:
        pass
    await call.answer(f"DND: {'ВКЛ' if state['dnd'] else 'ВЫКЛ'}")

@router.callback_query(F.data.in_(["on", "off"]))
async def toggle(call: CallbackQuery):
    state["enabled"] = (call.data == "on")
    save_state()
    try:
        await call.message.edit_text(
            f"Мост: {'🟢 ВКЛ' if state['enabled'] else '🔴 ВЫКЛ'}",
            reply_markup=main_kb()
        )
    except:
        pass
    await call.answer()

@router.callback_query(F.data == "status")
async def status_check(call: CallbackQuery):
    status = "🟢 Онлайн" if state["enabled"] else "🔴 Оффлайн"
    await call.answer(
        f"{status}\nDND: {state.get('dnd')}\nКанал: {state['discord_channel_id']}",
        show_alert=True
    )

@router.callback_query(F.data == "set_channel")
async def set_channel_req(call: CallbackQuery):
    await call.message.answer("✏️ Отправь ID канала Discord")
    await call.answer()

@router.message(F.text.regexp(r"^\d{17,20}$"))
async def update_channel(msg: Message):
    if msg.chat.id != state.get("tg_chat_id"):
        return
    state["discord_channel_id"] = int(msg.text)
    save_state()
    await msg.answer(f"✅ Канал → {msg.text}")

# ───────── WEBHOOK ─────────
async def get_webhook(channel):
    try:
        webhooks = await channel.webhooks()
        for wh in webhooks:
            if wh.name == "Bridge":
                return wh
        return await channel.create_webhook(name="Bridge")
    except Exception as e:
        print(f"❌ Не удалось создать/найти webhook: {e}")
        return None

# ───────── TG → DC: новое сообщение ─────────
@router.message()
async def tg_to_dc(msg: Message):
    if not state["enabled"] or msg.chat.id != state.get("tg_chat_id"):
        return
    if msg.text and msg.text.startswith("/"):
        return

    path = None
    file_to_send = None

    try:
        guild = dc.get_guild(GUILD_ID)
        if not guild:
            return
        channel = guild.get_channel(state["discord_channel_id"])
        if not channel:
            return

        webhook = await get_webhook(channel)
        if not webhook:
            return

        content = (msg.text or msg.caption or "").strip()[:2000]

        is_sticker = bool(msg.sticker)

        if is_sticker:
            if msg.sticker.file_size and msg.sticker.file_size > MAX_FILE_SIZE:
                content = "Стикер > 8 MB"
            else:
                file_info = await bot.get_file(msg.sticker.file_id)
                path = os.path.join(TMP_DIR, f"st_{msg.sticker.file_id}.webp")
                await bot.download_file(file_info.file_path, path)
                file_to_send = File(path, filename="sticker.webp")
                # Для стикеров без подписи — НЕ ставим content вообще
                if not content:
                    content = None

        elif msg.photo or msg.document or msg.video or msg.animation or msg.voice or msg.audio:
            media = msg.photo[-1] if msg.photo else (msg.document or msg.video or msg.animation or msg.voice or msg.audio)
            if media.file_size and media.file_size > MAX_FILE_SIZE:
                content = "Файл > 8 MB"
            else:
                file_info = await bot.get_file(media.file_id)
                ext = file_info.file_path.split('.')[-1] or "bin"
                path = os.path.join(TMP_DIR, f"f_{media.file_id}.{ext}")
                await bot.download_file(file_info.file_path, path)
                file_to_send = File(path)

        # Если нет ни текста, ни медиа, ни стикера — минимальная заглушка
        if not content and not file_to_send:
            content = "…"

        # Reply-ссылка (только если есть текст)
        if content and msg.reply_to_message:
            dc_reply_id = state["reply_map"].get(str(msg.reply_to_message.message_id))
            if dc_reply_id:
                link = f"https://discord.com/channels/{GUILD_ID}/{channel.id}/{dc_reply_id}"
                content = f"⤴️ [В ответ]({link})\n{content}"

        payload = {
            "username": (msg.from_user.full_name or "Unknown")[:32],
            "wait": True
        }

        if content is not None:
            payload["content"] = content

        if file_to_send:
            payload["file"] = file_to_send

        # Аватар пользователя
        try:
            ups = await bot.get_user_profile_photos(msg.from_user.id, limit=1)
            if ups.total_count > 0:
                img = await bot.get_file(ups.photos[0][-1].file_id)
                payload["avatar_url"] = f"https://api.telegram.org/file/bot{TG_TOKEN}/{img.file_path}"
        except:
            pass

        sent = await webhook.send(**payload)

        state["reply_map"][str(msg.message_id)] = str(sent.id)
        state["reply_map"][str(sent.id)] = str(msg.message_id)
        save_state()

        print(f"TG→DC ok: {msg.message_id} → {sent.id} {'(стикер)' if is_sticker else ''}")

    except Exception as e:
        print(f"❌ TG→DC: {type(e).__name__}: {e}")
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass

# ───────── TG → DC: редактирование ─────────
@router.edited_message()
async def tg_edited_to_dc(msg: Message):
    if not state["enabled"] or msg.chat.id != state.get("tg_chat_id"):
        return

    dc_msg_id_str = state["reply_map"].get(str(msg.message_id))
    if not dc_msg_id_str:
        return

    try:
        guild = dc.get_guild(GUILD_ID)
        channel = guild.get_channel(state["discord_channel_id"])
        if not channel:
            return

        webhook = await get_webhook(channel)
        if not webhook:
            return

        new_content = (msg.text or msg.caption or "").strip()[:2000] or "…"

        await webhook.edit_message(
            message_id=int(dc_msg_id_str),
            content=new_content
        )
        print(f"Edit TG→DC ok: {msg.message_id} → {dc_msg_id_str}")

    except discord.NotFound:
        print(f"Edit TG→DC: уже удалено в DC {dc_msg_id_str}")
        state["reply_map"].pop(str(msg.message_id), None)
        state["reply_map"].pop(dc_msg_id_str, None)
        save_state()
    except Exception as e:
        print(f"❌ Edit TG→DC: {type(e).__name__}: {e}")

# ───────── DISCORD ─────────
intents = discord.Intents.default()
intents.message_content = True
dc = discord.Client(intents=intents)

@dc.event
async def on_ready():
    print(f"🟢 Discord готов: {dc.user}")

@dc.event
async def on_message(message: discord.Message):
    if message.author.bot or message.webhook_id:
        return
    if not state["enabled"] or state.get("dnd"):
        return
    if message.channel.id != state["discord_channel_id"]:
        return

    try:
        tg_reply_id = None
        if message.reference and message.reference.message_id:
            tg_reply_id = state["reply_map"].get(str(message.reference.message_id))

        header = f"<b>[DC | {message.author.display_name}]</b>"

        content = message.clean_content.strip()
        saved_files = []

        if message.attachments:
            for att in message.attachments:
                if att.size > 50_000_000:
                    await bot.send_message(
                        state["tg_chat_id"],
                        f"{header}\nСлишком большой файл: {att.filename}",
                        reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                        parse_mode="HTML"
                    )
                    continue

                path = os.path.join(TMP_DIR, f"{att.id}_{att.filename}")
                await att.save(path)
                saved_files.append(path)

                caption = f"{header}\n{content}" if att == message.attachments[0] and content else f"{header}\n{att.filename}"
                sent = await bot.send_document(
                    state["tg_chat_id"],
                    FSInputFile(path),
                    caption=caption,
                    reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                    parse_mode="HTML"
                )

                state["reply_map"][str(message.id)] = str(sent.message_id)
                state["reply_map"][str(sent.message_id)] = str(message.id)

        elif message.stickers:
            for sticker in message.stickers:
                sent = await bot.send_message(
                    state["tg_chat_id"],
                    f"{header}\nСтикер: {sticker.url}",
                    reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                    parse_mode="HTML"
                )
                state["reply_map"][str(message.id)] = str(sent.message_id)
                state["reply_map"][str(sent.message_id)] = str(message.id)

        elif content:
            sent = await bot.send_message(
                state["tg_chat_id"],
                f"{header}\n{content}",
                reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                parse_mode="HTML"
            )
            state["reply_map"][str(message.id)] = str(sent.message_id)
            state["reply_map"][str(sent.message_id)] = str(message.id)

        save_state()

        print(f"DC→TG ok: {message.id} → {state['reply_map'].get(str(message.id), '?')}")

    except Exception as e:
        print(f"❌ DC→TG: {type(e).__name__}: {e}")
    finally:
        for p in saved_files:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass

@dc.event
async def on_message_edit(before, after):
    if after.author.bot or after.webhook_id:
        return
    if not state["enabled"] or state.get("dnd"):
        return
    if after.channel.id != state["discord_channel_id"]:
        return

    tg_msg_id_str = state["reply_map"].get(str(after.id))
    if not tg_msg_id_str:
        return

    try:
        header = f"<b>[DC | {after.author.display_name}]</b> ✏️"
        new_content = after.clean_content.strip() or "…"

        await bot.edit_message_text(
            chat_id=state["tg_chat_id"],
            message_id=int(tg_msg_id_str),
            text=f"{header}\n{new_content}",
            parse_mode="HTML"
        )
        print(f"Edit DC→TG ok: {after.id} → {tg_msg_id_str}")

    except Exception as e:
        print(f"❌ Edit DC→TG: {type(e).__name__}: {e}")

@dc.event
async def on_message_delete(message):
    if message.author.bot or message.webhook_id:
        return
    if not state["enabled"] or state.get("dnd"):
        return
    if message.channel.id != state["discord_channel_id"]:
        return

    tg_msg_id_str = state["reply_map"].get(str(message.id))
    if not tg_msg_id_str:
        return

    try:
        await bot.delete_message(
            chat_id=state["tg_chat_id"],
            message_id=int(tg_msg_id_str)
        )
        print(f"Delete DC→TG ok: {message.id} → {tg_msg_id_str} (удалено)")

        state["reply_map"].pop(str(message.id), None)
        state["reply_map"].pop(tg_msg_id_str, None)
        save_state()

    except Exception as e:
        print(f"❌ Delete DC→TG: {type(e).__name__}: {e}")
        state["reply_map"].pop(str(message.id), None)
        state["reply_map"].pop(tg_msg_id_str, None)
        save_state()

# ───────── RUN ─────────
async def main():
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    asyncio.create_task(dc.start(DC_TOKEN))
    await dp.start_polling(
        bot,
        allowed_updates=["message", "edited_message", "callback_query"]
    )

if __name__ == "__main__":
    asyncio.run(main())
