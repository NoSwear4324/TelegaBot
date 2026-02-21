import os
import json
import asyncio
import shutil
from datetime import timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import CommandStart
import discord
from discord import Webhook, File
from discord.utils import get as discord_get

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
ALL_USERS_FILE = "all_users.json"

# Загружаем всех пользователей
all_users = []
if os.path.exists(ALL_USERS_FILE):
    try:
        with open(ALL_USERS_FILE, "r", encoding="utf-8") as f:
            all_users = json.load(f)
    except:
        pass

state = {
    "enabled": True,
    "dnd": False,
    "admins": [],  # Список админов
    "allowed_users": [],  # Список разрешённых пользователей
    "discord_channel_id": DEFAULT_CHANNEL_ID,
    "reply_map": {}
}

def save_state():
    if len(state["reply_map"]) > 3000:
        keys = list(state["reply_map"].keys())
        for k in keys[:1500]:
            state["reply_map"].pop(k, None)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def save_all_users():
    """Сохранить всех пользователей"""
    with open(ALL_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_users, f, indent=2, ensure_ascii=False)

def add_user_to_all(chat_id):
    """Добавить пользователя в список всех пользователей"""
    if chat_id not in all_users:
        all_users.append(chat_id)
        save_all_users()

def load_state():
    """Загрузить состояние из файла"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                loaded = json.load(f)
                state.update(loaded)
                # Миграция: если есть admin_chat_id, переносим в admins
                if "admin_chat_id" in loaded and loaded["admin_chat_id"]:
                    if loaded["admin_chat_id"] not in state["admins"]:
                        state["admins"].append(loaded["admin_chat_id"])
                    del state["admin_chat_id"]
                    save_state()
                # Миграция: если есть tg_chat_id, переносим в admins
                if "tg_chat_id" in loaded and loaded["tg_chat_id"]:
                    if loaded["tg_chat_id"] not in state["admins"]:
                        state["admins"].append(loaded["tg_chat_id"])
                    if loaded["tg_chat_id"] not in state["allowed_users"]:
                        state["allowed_users"].append(loaded["tg_chat_id"])
                    del state["tg_chat_id"]
                    save_state()
            except Exception as e:
                print(f"⚠️ Ошибка загрузки state: {e}")

load_state()

def is_admin(chat_id):
    """Проверка, является ли пользователь админом"""
    return chat_id in state.get("admins", [])

def is_allowed(chat_id):
    """Проверка, разрешён ли пользователь — теперь все разрешены"""
    return True

def add_admin(chat_id):
    """Добавить админа"""
    if chat_id not in state["admins"]:
        state["admins"].append(chat_id)
        save_state()

def remove_admin(chat_id):
    """Удалить админа"""
    if chat_id in state["admins"]:
        state["admins"].remove(chat_id)
        save_state()

def add_allowed_user(chat_id):
    """Добавить пользователя в список (для статистики)"""
    if chat_id not in state["allowed_users"]:
        state["allowed_users"].append(chat_id)
        save_state()

def remove_allowed_user(chat_id):
    """Удалить пользователя из списка (для статистики)"""
    if chat_id in state["allowed_users"]:
        state["allowed_users"].remove(chat_id)
        save_state()

async def send_to_all_users(text, **kwargs):
    """Отправить сообщение всем пользователям"""
    all_chats = list(set(all_users))  # Используем всех пользователей
    sent_messages = []
    for chat_id in all_chats:
        if chat_id:
            try:
                msg = await bot.send_message(chat_id, text, **kwargs)
                sent_messages.append((chat_id, msg.message_id))
            except Exception as e:
                print(f"⚠️ Не удалось отправить в {chat_id}: {e}")
    return sent_messages

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
        ],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="users")]
    ])

@router.message(CommandStart())
async def start(msg: Message):
    # Добавляем пользователя в список всех пользователей
    add_user_to_all(msg.chat.id)
    
    # Первый пользователь становится админом
    if not state.get("admins"):
        add_admin(msg.chat.id)
        add_allowed_user(msg.chat.id)

    # Сброс состояния для админа
    if is_admin(msg.chat.id):
        state["enabled"] = True
        state["dnd"] = False
        state["discord_channel_id"] = DEFAULT_CHANNEL_ID
        state["reply_map"] = {}
        save_state()
        await msg.answer(f"🚀 Мост TG ↔ DC запущен\nВы АДМИН\nАдминов: {len(state.get('admins', []))}\nПользователей: {len(all_users)}", reply_markup=main_kb())
    else:
        # Все остальные пользователи имеют доступ
        await msg.answer("🚀 Мост TG ↔ DC работает\nДоступ разрешён", reply_markup=main_kb())

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
        f"{status}\nDND: {state.get('dnd')}\nКанал: {state['discord_channel_id']}\nАдминов: {len(state.get('admins', []))}\nПользователей: {len(all_users)}",
        show_alert=True
    )

@router.callback_query(F.data == "set_channel")
async def set_channel_req(call: CallbackQuery):
    await call.message.answer("✏️ Отправь ID канала Discord")
    await call.answer()

@router.callback_query(F.data == "users")
async def users_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Только для админа", show_alert=True)
        return

    admins_list = "\n".join([f"👑 {u}" for u in state.get("admins", [])])
    users_list = "\n".join([f"• {u}" for u in all_users if u not in state.get("admins", [])])

    await call.message.answer(
        f"👥 Админы ({len(state.get('admins', []))}):\n{admins_list}\n\n"
        f"👤 Пользователи ({len(all_users) - len(state.get('admins', []))}):\n{users_list if users_list else '—'}\n\n"
        f"➕ Добавить админа: +ID (например +123456)\n"
        f"➖ Удалить админа: -ID (например -123456)\n"
        f"➕ Добавить пользователя: ID\n"
        f"➖ Удалить пользователя: -ID",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="users_refresh")]
        ])
    )
    await call.answer()

@router.callback_query(F.data == "users_refresh")
async def users_refresh(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    # Закрываем старое меню и открываем новое
    await call.message.delete()
    admins_list = "\n".join([f"👑 {u}" for u in state.get("admins", [])])
    users_list = "\n".join([f"• {u}" for u in all_users if u not in state.get("admins", [])])

    await call.message.answer(
        f"👥 Админы ({len(state.get('admins', []))}):\n{admins_list}\n\n"
        f"👤 Пользователи ({len(all_users) - len(state.get('admins', []))}):\n{users_list if users_list else '—'}\n\n"
        f"➕ Добавить админа: +ID (например +123456)\n"
        f"➖ Удалить админа: -ID (например -123456)\n"
        f"➕ Добавить пользователя: ID\n"
        f"➖ Удалить пользователя: -ID",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="users_refresh")]
        ])
    )
    await call.answer()

@router.message(F.text.regexp(r"^\d{17,20}$"))
async def update_channel(msg: Message):
    if not is_admin(msg.chat.id):
        return
    state["discord_channel_id"] = int(msg.text)
    save_state()
    await msg.answer(f"✅ Канал → {msg.text}")

@router.message(F.text.regexp(r"^-\d+$"))
async def remove_admin_or_user(msg: Message):
    if not is_admin(msg.chat.id):
        return
    user_id = int(msg.text)
    if user_id in state["admins"]:
        remove_admin(user_id)
        remove_allowed_user(user_id)
        await msg.answer(f"❌ Админ {user_id} удалён")
    elif user_id in state.get("allowed_users", []):
        remove_allowed_user(user_id)
        await msg.answer(f"❌ Пользователь {user_id} удалён")
    else:
        await msg.answer(f"⚠️ {user_id} не найден в списке")

@router.message(F.text.regexp(r"^\+\d+$"))
async def add_admin_cmd(msg: Message):
    if not is_admin(msg.chat.id):
        return
    user_id = int(msg.text.replace("+", ""))
    add_admin(user_id)
    add_allowed_user(user_id)
    await msg.answer(f"✅ Админ {user_id} добавлен")

@router.message(F.text.regexp(r"^\d{8,15}$"))
async def add_user(msg: Message):
    if not is_admin(msg.chat.id):
        return
    # Проверяем, не канал ли это
    text = msg.text.strip()
    if len(text) >= 17:  # Это ID канала
        return
    user_id = int(text)
    add_allowed_user(user_id)
    await msg.answer(f"✅ Пользователь {user_id} добавлен")

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
    # Добавляем пользователя в список всех (если это новый пользователь)
    add_user_to_all(msg.chat.id)
    
    # Проверяем доступ
    if not is_allowed(msg.chat.id):
        return

    if not state["enabled"]:
        return

    if msg.text and msg.text.startswith("/"):
        return

    path = None
    file_to_send = None

    # Формируем заголовок с именем отправителя
    sender_name = msg.from_user.full_name or "Unknown"
    tg_header = f"<b>[TG | {sender_name}]</b>"

    # Получаем контент
    content = (msg.text or msg.caption or "").strip()[:2000]
    if content:
        content_with_header = f"{tg_header}\n{content}"
    else:
        content_with_header = tg_header

    # Отправляем сообщение всем пользователям Telegram (кроме отправителя)
    all_chats = list(set(all_users))
    sent_tg_messages = {}  # chat_id -> message_id
    for chat_id in all_chats:
        if chat_id == msg.chat.id:
            continue  # Не отправляем самому себе
        try:
            if not (msg.photo or msg.document or msg.video or msg.animation or msg.voice or msg.audio or msg.sticker or msg.video_note):
                # Только текст
                sent = await bot.send_message(
                    chat_id,
                    content_with_header,
                    reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
                    parse_mode="HTML"
                )
                sent_tg_messages[chat_id] = sent.message_id
        except Exception as e:
            print(f"⚠️ Не удалось отправить в TG {chat_id}: {e}")

    # Если это только текст — отправляем в Discord
    if not (msg.photo or msg.document or msg.video or msg.animation or msg.voice or msg.audio or msg.sticker or msg.video_note):
        try:
            guild = dc.get_guild(GUILD_ID)
            if not guild:
                return
            channel = guild.get_channel(state["discord_channel_id"])
            if not channel:
                return

            # Аватар пользователя
            avatar_url = None
            try:
                ups = await bot.get_user_profile_photos(msg.from_user.id, limit=1)
                if ups.total_count > 0:
                    img = await bot.get_file(ups.photos[0][-1].file_id)
                    avatar_url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{img.file_path}"
            except:
                pass

            # Reply на сообщение из Discord
            if msg.reply_to_message:
                dc_reply_id = state["reply_map"].get(str(msg.reply_to_message.message_id))
                if dc_reply_id:
                    # Добавляем ссылку на сообщение в контент
                    reply_link = f"https://discord.com/channels/{GUILD_ID}/{channel.id}/{dc_reply_id}"
                    content = f"⤴️ [В ответ]({reply_link})\n{content}"

            # Обычная отправка через webhook
            webhook = await get_webhook(channel)
            if not webhook:
                return

            payload = {
                "username": sender_name[:32],
                "wait": True,
                "content": content
            }

            if avatar_url:
                payload["avatar_url"] = avatar_url

            sent = await webhook.send(**payload)
            state["reply_map"][str(msg.message_id)] = str(sent.id)
            state["reply_map"][str(sent.id)] = str(msg.message_id)
            save_state()
            print(f"TG→DC ok: {msg.message_id} → {sent.id}")
        except Exception as e:
            print(f"❌ TG→DC: {type(e).__name__}: {e}")
        return

    # Если есть медиа — продолжаем стандартную обработку

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

        # Определяем тип медиа
        is_sticker = bool(msg.sticker)
        is_video_note = bool(msg.video_note)  # кружочки
        is_voice = bool(msg.voice)
        is_video = bool(msg.video)
        is_animation = bool(msg.animation)  # GIF
        is_audio = bool(msg.audio)
        is_photo = bool(msg.photo)
        is_document = bool(msg.document)
        is_poll = bool(msg.poll)  # голосование

        # Обработка голосования из Telegram → нативный опрос Discord + текст с результатами
        if is_poll:
            poll = msg.poll
            poll_options = "\n".join([f"▫️ {opt.text} — {opt.voter_count}" for opt in poll.options])
            poll_type = "📊 Анонимный" if poll.is_anonymous else "📢 Открытый"
            poll_status = "✅ Завершено" if poll.is_closed else "🔓 Активно"

            # Текст с результатами
            results_text = f"{poll_type} опрос: {poll.question}\n\n{poll_options}\n{poll_status}"
            if msg.caption:
                results_text = f"{msg.caption.strip()}\n\n{results_text}"

            # Создаём нативный опрос Discord
            try:
                discord_poll = discord.Poll(
                    question=poll.question[:300],
                    duration=timedelta(hours=24),
                )
                for opt in poll.options[:10]:
                    discord_poll.add_answer(text=opt.text[:55])

                payload = {
                    "username": (msg.from_user.full_name or "Unknown")[:32],
                    "wait": True,
                    "content": results_text,
                    "poll": discord_poll
                }

                sent = await webhook.send(**payload)
                state["reply_map"][str(msg.message_id)] = str(sent.id)
                state["reply_map"][str(sent.id)] = str(msg.message_id)
                state["reply_map"][f"poll_{msg.message_id}"] = "tg"  # Помечаем что это TG опрос
                save_state()
                print(f"TG→DC poll ok: {msg.message_id} → {sent.id}")
                return
            except Exception as e:
                import traceback
                print(f"⚠️ Не удалось создать нативный опрос Discord: {e}")
                print(traceback.format_exc())
                # Фолбэк — только текст
                payload = {
                    "username": (msg.from_user.full_name or "Unknown")[:32],
                    "wait": True,
                    "content": results_text
                }
                sent = await webhook.send(**payload)
                state["reply_map"][str(msg.message_id)] = str(sent.id)
                state["reply_map"][str(sent.id)] = str(msg.message_id)
                state["reply_map"][f"poll_{msg.message_id}"] = "tg"  # Помечаем что это TG опрос
                save_state()
                print(f"TG→DC poll (text) ok: {msg.message_id} → {sent.id}")
                return

        if is_sticker:
            # Обработка стикеров Telegram → Discord
            sticker = msg.sticker
            if sticker.file_size and sticker.file_size > MAX_FILE_SIZE:
                content = "Стикер > 8 MB"
            else:
                file_info = await bot.get_file(sticker.file_id)
                # Определяем расширение по типу стикера
                if sticker.is_video:
                    ext = "webm"  # видео-стикеры
                    content = "🎬 Видео-стикер"
                elif sticker.is_animated:
                    ext = "tgs"  # анимированные (Lottie)
                    content = "✨ Анимированный стикер (Lottie)"
                elif sticker.type == "png":
                    ext = "png"
                elif sticker.type == "gif":
                    ext = "gif"
                else:
                    ext = "webp"  # обычные стикеры
                
                path = os.path.join(TMP_DIR, f"st_{sticker.file_id}.{ext}")
                await bot.download_file(file_info.file_path, path)
                file_to_send = File(path, filename=f"sticker.{ext}")
                if not content or content.startswith("Стикер") or content.startswith("🎬") or content.startswith("✨"):
                    content = None

        elif is_video_note:
            # Кружочки (video note)
            vn = msg.video_note
            if vn.file_size and vn.file_size > MAX_FILE_SIZE:
                content = "Кружочек > 8 MB"
            else:
                file_info = await bot.get_file(vn.file_id)
                path = os.path.join(TMP_DIR, f"vn_{vn.file_id}.mp4")
                await bot.download_file(file_info.file_path, path)
                file_to_send = File(path, filename="video_note.mp4")

        elif is_voice:
            # Голосовые сообщения
            voice = msg.voice
            if voice.file_size and voice.file_size > MAX_FILE_SIZE:
                content = "Голосовое > 8 MB"
            else:
                file_info = await bot.get_file(voice.file_id)
                ext = voice.mime_type.split('/')[-1] if voice.mime_type else "ogg"
                path = os.path.join(TMP_DIR, f"vc_{voice.file_id}.{ext}")
                await bot.download_file(file_info.file_path, path)
                file_to_send = File(path, filename=f"voice.{ext}")

        elif is_photo or is_document or is_video or is_animation or is_audio:
            # Остальные медиа
            media = msg.photo[-1] if is_photo else (msg.document or msg.video or msg.animation or msg.audio)
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

        # Отправляем медиа всем пользователям Telegram (кроме отправителя)
        for chat_id in all_chats:
            if chat_id == msg.chat.id:
                continue
            try:
                if file_to_send:
                    sent_tg = await bot.send_document(
                        chat_id,
                        file_to_send,
                        caption=f"{tg_header}\n{content}" if content else tg_header,
                        reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
                        parse_mode="HTML"
                    )
                elif is_video_note:
                    file_info = await bot.get_file(msg.video_note.file_id)
                    path = os.path.join(TMP_DIR, f"vn_{msg.video_note.file_id}.mp4")
                    await bot.download_file(file_info.file_path, path)
                    sent_tg = await bot.send_video_note(
                        chat_id,
                        FSInputFile(path),
                        reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None
                    )
                elif is_voice:
                    file_info = await bot.get_file(msg.voice.file_id)
                    ext = msg.voice.mime_type.split('/')[-1] if msg.voice.mime_type else "ogg"
                    path = os.path.join(TMP_DIR, f"vc_{msg.voice.file_id}.{ext}")
                    await bot.download_file(file_info.file_path, path)
                    sent_tg = await bot.send_voice(
                        chat_id,
                        FSInputFile(path),
                        caption=f"{tg_header}\n{content}" if content else tg_header,
                        reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
                        parse_mode="HTML"
                    )
            except Exception as e:
                print(f"⚠️ Не удалось отправить медиа в TG {chat_id}: {e}")

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
    if not state["enabled"] or not is_allowed(msg.chat.id):
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

# ───────── TG → DC: обновление опроса ─────────
@router.edited_message()
async def tg_poll_edited_to_dc(msg: Message):
    """Обновление голосования в Telegram → обновление в Discord"""
    if not state["enabled"]:
        return
    
    if not msg.poll:
        return  # Это не опрос
    
    dc_msg_id = state["reply_map"].get(str(msg.message_id))
    if not dc_msg_id:
        return
    
    try:
        guild = dc.get_guild(GUILD_ID)
        channel = guild.get_channel(state["discord_channel_id"])
        if not channel:
            return
        
        webhook = await get_webhook(channel)
        if not webhook:
            return
        
        poll = msg.poll
        poll_options = "\n".join([f"▫️ {opt.text} — {opt.voter_count}" for opt in poll.options])
        poll_type = "📊 Анонимный" if poll.is_anonymous else "📢 Открытый"
        poll_status = "✅ Завершено" if poll.is_closed else "🔓 Активно"
        
        new_content = f"{poll_type} опрос: {poll.question}\n\n{poll_options}\n{poll_status}"
        if msg.caption:
            new_content = f"{msg.caption.strip()}\n\n{new_content}"
        
        await webhook.edit_message(
            message_id=int(dc_msg_id),
            content=new_content
        )
        print(f"✅ Poll edited TG→DC ok: {msg.message_id} → {dc_msg_id}")
        
    except discord.NotFound:
        print(f"Poll edited TG→DC: уже удалено в DC {dc_msg_id}")
    except Exception as e:
        print(f"❌ Poll edited TG→DC: {type(e).__name__}: {e}")

# ───────── DISCORD ─────────
intents = discord.Intents.all()
intents.message_content = True
intents.polls = True
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
                    await send_to_all_users(
                        f"{header}\nСлишком большой файл: {att.filename}",
                        reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                        parse_mode="HTML"
                    )
                    continue

                path = os.path.join(TMP_DIR, f"{att.id}_{att.filename}")
                await att.save(path)
                saved_files.append(path)

                caption = f"{header}\n{content}" if att == message.attachments[0] and content else f"{header}\n{att.filename}"
                # Отправляем всем пользователям
                all_chats = list(set(all_users))
                for chat_id in all_chats:
                    try:
                        sent = await bot.send_document(
                            chat_id,
                            FSInputFile(path),
                            caption=caption,
                            reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                            parse_mode="HTML"
                        )
                        state["reply_map"][str(message.id)] = str(sent.message_id)
                        state["reply_map"][str(sent.message_id)] = str(message.id)
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить файл в {chat_id}: {e}")

        elif message.stickers:
            for sticker in message.stickers:
                sticker_url = sticker.url
                sticker_format = sticker.format

                if sticker.format in (discord.StickerFormatType.png, discord.StickerFormatType.apng, discord.StickerFormatType.gif):
                    try:
                        async with dc.http.get(sticker_url) as resp:
                            if resp.status == 200:
                                ext = "gif" if sticker.format == discord.StickerFormatType.gif else "png"
                                path = os.path.join(TMP_DIR, f"dc_sticker_{sticker.id}.{ext}")
                                with open(path, 'wb') as f:
                                    f.write(await resp.read())

                                caption = f"{header}\nСтикер"
                                if content and sticker == message.stickers[0]:
                                    caption = f"{header}\n{content}"

                                # Отправляем всем
                                all_chats = list(set(all_users))
                                for chat_id in all_chats:
                                    try:
                                        sent = await bot.send_document(
                                            chat_id,
                                            FSInputFile(path),
                                            caption=caption,
                                            reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                                            parse_mode="HTML"
                                        )
                                        state["reply_map"][str(message.id)] = str(sent.message_id)
                                        state["reply_map"][str(sent.message_id)] = str(message.id)
                                    except Exception as e:
                                        print(f"⚠️ Не удалось отправить в {chat_id}: {e}")
                                saved_files.append(path)
                                continue
                    except Exception as e:
                        print(f"⚠️ Не удалось скачать стикер DC: {e}")

                # Для Lottie или если не удалось скачать — отправляем ссылку
                sticker_type = "Lottie" if sticker.format == discord.StickerFormatType.lottie else "Стикер"
                all_chats = list(set(all_users))
                for chat_id in all_chats:
                    try:
                        sent = await bot.send_message(
                            chat_id,
                            f"{header}\n{sticker_type}: {sticker_url}",
                            reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                            parse_mode="HTML"
                        )
                        state["reply_map"][str(message.id)] = str(sent.message_id)
                        state["reply_map"][str(sent.message_id)] = str(message.id)
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить в {chat_id}: {e}")

        elif message.poll:
            poll = message.poll
            poll_options = "\n".join([f"{i+1}⃣ {opt.text} — {opt.vote_count}" for i, opt in enumerate(poll.answers)])
            poll_status = "✅ Завершено" if poll.is_finalized else "🔓 Активно"
            poll_text = f"📊 Опрос: {poll.question}\n\n{poll_options}\n\n{poll_status}"

            sent_list = await send_to_all_users(
                f"{header}\n{poll_text}",
                reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                parse_mode="HTML"
            )
            if sent_list:
                first_chat, first_msg_id = sent_list[0]
                state["reply_map"][str(message.id)] = str(first_msg_id)
                state["reply_map"][str(first_msg_id)] = str(message.id)
                state["reply_map"][f"poll_{first_msg_id}"] = "dc"
            save_state()
            print(f"DC→TG poll ok: {message.id} → {sent_list[0][1] if sent_list else 'N/A'}")

        elif content:
            sent_list = await send_to_all_users(
                f"{header}\n{content}",
                reply_to_message_id=int(tg_reply_id) if tg_reply_id else None,
                parse_mode="HTML"
            )
            if sent_list:
                first_chat, first_msg_id = sent_list[0]
                state["reply_map"][str(message.id)] = str(first_msg_id)
                state["reply_map"][str(first_msg_id)] = str(message.id)

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

        # Редактируем у всех пользователей
        all_chats = list(set(all_users))
        for chat_id in all_chats:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(tg_msg_id_str),
                    text=f"{header}\n{new_content}",
                    parse_mode="HTML"
                )
            except:
                pass
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
        # Удаляем у всех пользователей
        all_chats = list(set(all_users))
        for chat_id in all_chats:
            try:
                await bot.delete_message(
                    chat_id=chat_id,
                    message_id=int(tg_msg_id_str)
                )
            except:
                pass
        print(f"Delete DC→TG ok: {message.id} → {tg_msg_id_str} (удалено)")

        state["reply_map"].pop(str(message.id), None)
        state["reply_map"].pop(tg_msg_id_str, None)
        save_state()

    except Exception as e:
        print(f"❌ Delete DC→TG: {type(e).__name__}: {e}")
        state["reply_map"].pop(str(message.id), None)
        state["reply_map"].pop(tg_msg_id_str, None)
        save_state()

# ───────── DC: Обновление опросов ─────────
@dc.event
async def on_raw_poll_vote_add(payload):
    """Обновление голосования Discord → обновление текста в Telegram"""
    if not state["enabled"]:
        return
    
    channel_id = int(payload.channel_id)
    if channel_id != state["discord_channel_id"]:
        return
    
    tg_msg_id = state["reply_map"].get(str(payload.message_id))
    if not tg_msg_id:
        return
    
    # Проверяем, не является ли это TG опросом (их нельзя редактировать)
    poll_key = f"poll_{tg_msg_id}"
    if state["reply_map"].get(poll_key) == "tg":
        print(f"⛔ Skip TG poll update: {tg_msg_id}")
        return
    
    try:
        channel = dc.get_channel(channel_id)
        message = await channel.fetch_message(payload.message_id)
        
        if not message.poll:
            return
        
        poll = message.poll
        poll_options = "\n".join([f"{i+1}⃣ {opt.text} — {opt.vote_count}" for i, opt in enumerate(poll.answers)])
        poll_status = "✅ Завершено" if poll.is_finalized else "🔓 Активно"
        poll_text = f"📊 Опрос: {poll.question}\n\n{poll_options}\n\n{poll_status}"

        author_name = message.author.display_name if hasattr(message, 'author') and message.author else "Unknown"

        # Редактируем у всех пользователей
        all_chats = list(set(all_users))
        for chat_id in all_chats:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(tg_msg_id),
                    text=f"<b>[DC | {author_name}]</b>\n{poll_text}",
                    parse_mode="HTML"
                )
            except:
                pass
        print(f"✅ Poll vote DC→TG update: {payload.message_id}")
    except Exception as e:
        print(f"❌ Poll vote DC→TG: {e}")

@dc.event
async def on_raw_poll_vote_remove(payload):
    """Удаление голоса Discord → обновление текста в Telegram"""
    await on_raw_poll_vote_add(payload)

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

