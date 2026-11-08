# bot.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import asyncio
import threading
import time
import re
import httpx

# Импорты для базы данных
from database import db_manager, get_or_create_user, create_meeting, get_user_meetings, get_meeting_by_id
from database import update_meeting_field, update_meeting_participants, create_group_announcement
from database import get_group_announcement, create_scheduled_post_with_title, get_scheduled_posts
from database import get_scheduled_post_by_job_id, update_scheduled_post_status
from database import save_user_group, get_user_groups, get_group_by_id, deactivate_group
from telegram.request import HTTPXRequest
from config import BOT_TOKEN, DATABASE_URL, LOG_DIR, PROXY_URL
from sqlalchemy import text

# Создаем папку для логов
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# Дополнительный логгер для пользовательских действий
user_logger = logging.getLogger('user_actions')
user_handler = logging.FileHandler(LOG_DIR / "users.log", encoding='utf-8')
user_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
user_logger.addHandler(user_handler)
user_logger.setLevel(logging.INFO)

# Логгер для всех сообщений
message_logger = logging.getLogger('all_messages')
message_handler = logging.FileHandler(LOG_DIR / "messages.log", encoding='utf-8')
message_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
message_logger.addHandler(message_handler)
message_logger.setLevel(logging.INFO)

# Используйте ваш реальный токен
BOT_TOKEN = "8067436187:AAFiv3PWNwecdNNu6exg8p93hmLUT6SLRY0"

# Глобальная переменная для приложения
#   app_instance = None

# Временные хранилища (для активных проверок)
scheduled_posts = {}

def escape_markdown(text):
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# Функции для логирования
def log_user_action(user_id: int, username: str, first_name: str, action: str, details: str = ""):
    """Логирует действия пользователя"""
    user_info = f"ID: {user_id}"
    if username:
        user_info += f" | @{username}"
    if first_name:
        user_info += f" | {first_name}"
    
    log_message = f"USER: {user_info} | ACTION: {action}"
    if details:
        log_message += f" | DETAILS: {details}"
    
    user_logger.info(log_message)

def log_message(user_id: int, username: str, first_name: str, chat_type: str, message_type: str, content: str):
    """Логирует все сообщения"""
    user_info = f"ID: {user_id}"
    if username:
        user_info += f" | @{username}"
    if first_name:
        user_info += f" | {first_name}"
    
    log_message = f"USER: {user_info} | CHAT: {chat_type} | TYPE: {message_type} | CONTENT: {content[:200]}"
    message_logger.info(log_message)

# Декораторы для логирования
def log_command(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message:
            user = update.effective_user
            command = update.message.text
            
            log_user_action(
                user.id if user else 0, 
                user.username if user else "", 
                user.first_name if user else "", 
                "COMMAND", 
                command
            )
            
            if user:
                log_message(
                    user.id,
                    user.username,
                    user.first_name,
                    update.effective_chat.type,
                    "command",
                    command
                )
        
        return await func(update, context)
    return wrapper

def log_message_handler(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and update.message.text:
            user = update.effective_user
            if user:
                log_message(
                    user.id,
                    user.username,
                    user.first_name,
                    update.effective_chat.type,
                    "message",
                    update.message.text
                )
        
        return await func(update, context)
    return wrapper

def log_callback_handler(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            user = update.callback_query.from_user
            
            log_user_action(
                user.id,
                user.username,
                user.first_name,
                "CALLBACK",
                update.callback_query.data
            )
        
        return await func(update, context)
    return wrapper

# Команда /start
@log_command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "STARTED_BOT")
        
        # Сохраняем пользователя в БД
        with db_manager.get_session() as session:
            get_or_create_user(
                session,
                user.id,
                user.username,
                user.first_name,
                user.last_name
            )
    
    await update.message.reply_text(
        "🤖 Привет! Я бот для организации митингов.\n\n"
        "Создавайте встречи в этом чате, а я опубликую их в вашей группе!\n\n"
        "Доступные команды:\n"
        "/new_meeting - создать новую встречу\n"
        "/my_meetings - мои встречи\n"
        "/edit_meeting - редактировать встречу\n"
        "/publish - опубликовать встречу в группе\n"
        "/schedule - отложенная публикация\n"
        "/my_schedules - мои отложенные публикации\n"
        "/get_group_id - получить ID группы\n"
        "/my_groups - мои сохраненные группы\n"
        "/manage_groups - управление группами\n"
        "/help - помощь"
    )

# Команда для получения ID группы
@log_command
async def get_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type
    
    if user:
        log_user_action(user.id, user.username, user.first_name, "REQUESTED_GROUP_ID", f"chat_id: {chat_id}")
    
    if chat_type in ["group", "supergroup"]:
        # Автоматически сохраняем группу
        with db_manager.get_session() as session:
            save_user_group(
                session,
                user.id,
                chat_id,
                update.effective_chat.title,
                chat_type
            )
        
        await update.message.reply_text(
            f"📋 Информация о группе:\n"
            f"🆔 ID группы: `{chat_id}`\n"
            f"📝 Тип: {chat_type}\n"
            f"💡 Название: {update.effective_chat.title}\n\n"
            f"✅ **Группа сохранена!** Теперь вы можете публиковать встречи без ввода ID.\n\n"
            f"Используйте команды:\n"
            f"/my_groups - мои группы\n"
            f"/publish - опубликовать встречу",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            "❌ Эта команда работает только в группах!\n"
            "Добавьте меня в группу и используйте /get_group_id там."
        )

# Команда для просмотра сохраненных групп
@log_command
async def my_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "VIEWED_MY_GROUPS")
    
    with db_manager.get_session() as session:
        groups = get_user_groups(session, user.id)
    
    if not groups:
        await update.message.reply_text(
            "📝 У вас нет сохраненных групп.\n\n"
            "Чтобы добавить группу:\n"
            "1. Добавьте бота в группу\n"
            "2. Напишите в группе: /get_group_id\n"
            "3. Группа автоматически сохранится!"
        )
        return
    
    text = "📋 Ваши сохраненные группы:\n\n"
    for i, group in enumerate(groups, 1):
        text += f"{i}. **{group.group_title or 'Без названия'}**\n"
        text += f"   🆔 ID: `{group.group_chat_id}`\n"
        text += f"   📝 Тип: {group.group_type}\n"
        text += f"   📅 Добавлена: {group.created_at.strftime('%d.%m.%Y')}\n\n"
    
    text += "💡 Используйте /publish чтобы выбрать группу для публикации."
    
    await update.message.reply_text(text, parse_mode='Markdown')

# Команда для управления группами
@log_command
async def manage_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "MANAGE_GROUPS")
    
    with db_manager.get_session() as session:
        groups = get_user_groups(session, user.id)
    
    if not groups:
        await update.message.reply_text(
            "📝 У вас нет сохраненных групп.\n\n"
            "Чтобы добавить группу:\n"
            "1. Добавьте бота в группу\n"
            "2. Напишите в группе: /get_group_id\n"
            "3. Группа автоматически сохранится!"
        )
        return
    
    text = "🗂️ Управление группами:\n\n"
    keyboard = []
    
    for group in groups:
        text += f"• **{group.group_title or 'Группа'}**\n"
        text += f"  ID: `{group.group_chat_id}`\n"
        text += f"  Добавлена: {group.created_at.strftime('%d.%m.%Y')}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(
                f"❌ Удалить {group.group_title}", 
                callback_data=f"delete_group_{group.id}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="groups_back")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Создание новой встречи
@log_command
async def new_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user:
        log_user_action(user.id, user.username, user.first_name, "STARTED_NEW_MEETING")
    
    # Проверяем, что это личный чат
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Создавайте встречи в личном чате с ботом!")
        return
    
    # Сохраняем пользователя в БД
    with db_manager.get_session() as session:
        get_or_create_user(
            session,
            user.id,
            user.username,
            user.first_name,
            user.last_name
        )
    
    # Предлагаем выбрать тип встречи
    keyboard = [
        [InlineKeyboardButton("🏢 Офлайн встреча", callback_data="type_offline")],
        [InlineKeyboardButton("💻 Онлайн встреча", callback_data="type_online")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Выберите тип встречи:",
        reply_markup=reply_markup
    )

# Обработка callback'ов
@log_callback_handler
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("type_"):
        meeting_type = "offline" if "offline" in data else "online"
        log_user_action(user.id, user.username, user.first_name, "SELECTED_MEETING_TYPE", meeting_type)
        
        # Сохраняем тип встречи в контексте
        context.user_data["meeting_type"] = meeting_type
        context.user_data["step"] = "title"
        
        await query.edit_message_text(
            "📝 Введите название встречи:"
        )
    
    elif data.startswith("confirm_"):
        meeting_id = int(data.split("_")[1])
        log_user_action(user.id, user.username, user.first_name, "CONFIRMED_MEETING", f"meeting_id: {meeting_id}")
        await confirm_meeting(query, context, user.id, meeting_id)
    
    elif data == "cancel":
        cleanup_creation_context(context)
        await query.edit_message_text("❌ Создание встречи отменено.")
    
    elif data.startswith("edit_select_"):
        meeting_id = int(data.split("_")[2])
        await show_edit_menu(update, context, user.id, meeting_id)
    
    elif data.startswith("edit_field_"):
        parts = data.split("_")
        if len(parts) >= 3:
            meeting_id = int(parts[2])
            field = parts[3] if len(parts) > 3 else None
            await start_edit_field(update, context, user.id, meeting_id, field)
    
    elif data.startswith("group_list_"):
        parts = data.split("_")
        if len(parts) >= 5:
            creator_id = int(parts[2])
            meeting_id = int(parts[3])
            group_chat_id = int(parts[4])
            await show_participants_list(update, context, creator_id, meeting_id, group_chat_id)
    
    elif data == "groups_back":
        await query.edit_message_text("🔙 Возврат в главное меню")

# Подтверждение встречи
async def confirm_meeting(query, context, user_id: int, meeting_id: int):
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user_id, meeting_id)
        
    if not meeting:
        await query.edit_message_text("❌ Ошибка: встреча не найдена")
        return
        
    # Очищаем временные данные
    cleanup_creation_context(context)
    
    await query.edit_message_text(
        f"✅ Встреча '{meeting.title}' сохранена!\n\n"
        f"Теперь вы можете опубликовать её в группе:\n"
        f"1. Добавьте бота в группу\n"
        f"2. Используйте команду /publish\n"
        f"3. Или /my_meetings для управления встречами"
    )

# Обработка текстовых сообщений
@log_message_handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Проверяем, что это личный чат
    if update.effective_chat.type != "private":
        return
    
    text = update.message.text.strip()
    
    # Проверяем, находимся ли мы в процессе создания встречи
    if "step" in context.user_data:
        await handle_meeting_creation(update, context, user, text)
        return
    
    # Проверяем другие процессы (публикация, редактирование и т.д.)
    if await handle_other_processes(update, context, user, text):
        return
    
    # Если сообщение не обработано другими обработчиками
    await update.message.reply_text(
        "🤔 Не понял ваше сообщение. Используйте команды из меню или /help для справки."
    )

# Обработка создания встречи
async def handle_meeting_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str):
    step = context.user_data.get("step", "")
    
    # Логируем ввод данных для встречи
    log_user_action(user.id, user.username, user.first_name, f"MEETING_{step.upper()}", text[:100])
    
    if step == "title":
        context.user_data["title"] = text
        context.user_data["step"] = "description"
        await update.message.reply_text("📋 Введите описание встречи:")
    
    elif step == "description":
        context.user_data["description"] = text
        context.user_data["step"] = "date"
        await update.message.reply_text("📅 Введите дату встречи (ДД.ММ.ГГГГ):")
    
    elif step == "date":
        try:
            datetime.strptime(text, "%d.%m.%Y")
            context.user_data["date"] = text
            context.user_data["step"] = "time"
            await update.message.reply_text("⏰ Введите время встречи (ЧЧ:ММ):")
        except ValueError:
            await update.message.reply_text("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
    
    elif step == "time":
        try:
            datetime.strptime(text, "%H:%M")
            context.user_data["time"] = text
            
            if context.user_data["meeting_type"] == "offline":
                context.user_data["step"] = "location"
                await update.message.reply_text("📍 Введите место встречи:")
            else:
                context.user_data["step"] = "online_link"
                await update.message.reply_text("🔗 Введите ссылку для онлайн встречи:")
        
        except ValueError:
            await update.message.reply_text("❌ Неверный формат времени. Используйте ЧЧ:ММ")
    
    elif step == "location":
        context.user_data["location"] = text
        await save_meeting_to_db(update, context, user.id)
    
    elif step == "online_link":
        context.user_data["location"] = text
        await save_meeting_to_db(update, context, user.id)

# Сохранение встречи в базу данных
async def save_meeting_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Сохраняет встречу в БД и показывает превью"""
    try:
        # Явно создаем meeting_data с пустым списком участников
        meeting_data = {
            "type": context.user_data["meeting_type"],
            "title": context.user_data["title"],
            "description": context.user_data["description"],
            "date": context.user_data["date"],
            "time": context.user_data["time"],
            "location": context.user_data["location"],
            "participants": []  # ЯВНО указываем пустой список
        }
        
        logging.info(f"🔄 SAVE_MEETING: Сохранение встречи с данными: {meeting_data}")
        
        with db_manager.get_session() as session:
            meeting = create_meeting(session, user_id, meeting_data)
            
            # Проверяем, что участники действительно пустые
            logging.info(f"🔍 SAVE_MEETING: Проверка участников после создания: {meeting.participants}")
            
            context.user_data["current_meeting_id"] = meeting.id
        
        await show_meeting_preview(update, context, user_id, meeting.id)
        
    except Exception as e:
        logging.error(f"❌ Ошибка в save_meeting_to_db: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении встречи")

# Показать превью встречи
async def show_meeting_preview(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, meeting_id: int):
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user_id, meeting_id)
        
    if not meeting:
        await update.message.reply_text("❌ Ошибка: встреча не найдена")
        return
        
    meeting_type = "🏢 Офлайн" if meeting.meeting_type == "offline" else "💻 Онлайн"
    
    text = f"""
📋 Превью встречи:

📌 Название: {meeting.title}
📝 Описание: {meeting.description}
📅 Дата: {meeting.date}
⏰ Время: {meeting.time}
📍 Место: {meeting.location}
🔰 Тип: {meeting_type}
"""
    
    keyboard = [
        [InlineKeyboardButton("✅ Сохранить встречу", callback_data=f"confirm_{meeting.id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, reply_markup=reply_markup)

# Обработка других процессов (публикация, редактирование и т.д.)
async def handle_other_processes(update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str) -> bool:
    """Обрабатывает другие процессы и возвращает True если сообщение обработано"""
    
    # Обработка ID группы для публикации
    if "publish_step" in context.user_data and context.user_data["publish_step"] == "group_id":
        try:
            group_chat_id = int(text)
            meeting_id = context.user_data["publish_meeting_id"]
            
            log_user_action(user.id, user.username, user.first_name, "ENTERED_GROUP_ID", f"group_id: {group_chat_id}, meeting_id: {meeting_id}")
            
            # Пытаемся отправить тестовое сообщение в группу
            try:
                await context.bot.send_message(
                    chat_id=group_chat_id,
                    text="🔔 Бот для встреч подключен! Скоро здесь появится анонс встречи..."
                )
                
                # Публикуем встречу
                await publish_meeting_to_group(update, context, user.id, meeting_id, group_chat_id)
                
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Не удалось отправить сообщение в группу.\n"
                    f"Убедитесь, что:\n"
                    f"1. Бот добавлен в группу\n"
                    f"2. Бот имеет права на отправку сообщений\n"
                    f"3. ID группы указан правильно\n\n"
                    f"Ошибка: {str(e)}"
                )
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат ID группы. Введите число (например: -100123456789)")
        return True
    
    # Обработка редактирования встречи
    if "edit_step" in context.user_data and context.user_data["edit_step"] == "waiting_input":
        await handle_edit_input(update, context, user, text)
        return True
    
    # Обработка ID группы для отложенной публикации
    if "schedule_step" in context.user_data and context.user_data["schedule_step"] == "group_id":
        try:
            group_chat_id = int(text)
            meeting_id = context.user_data["schedule_meeting_id"]
            
            log_user_action(user.id, user.username, user.first_name, "ENTERED_SCHEDULE_GROUP_ID", 
                           f"group_id: {group_chat_id}, meeting_id: {meeting_id}")
            
            # Проверяем, что бот может писать в группу
            try:
                await context.bot.send_message(
                    chat_id=group_chat_id,
                    text="🔔 Бот для встреч подключен! Готов к отложенной публикации..."
                )
                
                # Переходим к выбору времени
                context.user_data["schedule_group_id"] = group_chat_id
                context.user_data["schedule_step"] = "datetime"
                
                await update.message.reply_text(
                    "✅ Группа подключена!\n\n"
                    "Теперь введите дату и время публикации в формате:\n"
                    "`ДД.ММ.ГГГГ ЧЧ:ММ`\n\n"
                    "Пример: `25.12.2024 14:30`\n\n"
                    "Время должно быть в будущем!"
                )
                
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Не удалось отправить сообщение в группу.\n"
                    f"Убедитесь, что:\n"
                    f"1. Бот добавлен в группу\n"
                    f"2. Бот имеет права на отправку сообщений\n"
                    f"3. ID группы указан правильно\n\n"
                    f"Ошибка: {str(e)}"
                )
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат ID группы. Введите число (например: -100123456789)")
        return True
    
    # Обработка времени для отложенной публикации
    if "schedule_step" in context.user_data and context.user_data["schedule_step"] == "datetime":
        try:
            datetime_str = text
            schedule_time = datetime.strptime(datetime_str, "%d.%m.%Y %H:%M")
            
            # Проверяем, что время в будущем
            if schedule_time <= datetime.now():
                await update.message.reply_text("❌ Время должно быть в будущем!")
                return True
            
            meeting_id = context.user_data["schedule_meeting_id"]
            group_chat_id = context.user_data["schedule_group_id"]
            
            log_user_action(user.id, user.username, user.first_name, "SET_SCHEDULE_TIME", 
                        f"time: {datetime_str}, meeting_id: {meeting_id}")
            
            # Создаем отложенную публикацию
            await create_scheduled_post_handler(update, context, user.id, meeting_id, group_chat_id, schedule_time)
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат даты и времени. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")
        return True
    
    return False

# Показать мои встречи
@log_command
async def my_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "VIEWED_MY_MEETINGS")
    
    with db_manager.get_session() as session:
        meetings = get_user_meetings(session, user.id)
    
    if not meetings:
        await update.message.reply_text("У вас пока нет созданных встреч.")
        return
    
    text = "📋 Ваши встречи:\n\n"
    for i, meeting in enumerate(meetings, 1):
        status = "✅ Опубликована" if meeting.published else "📝 Черновик"
        text += f"{i}. {meeting.title}\n"
        text += f"   📅 {meeting.date} в {meeting.time}\n"
        text += f"   📊 Статус: {status}\n\n"
    
    await update.message.reply_text(text)

# Команда для публикации встречи
@log_command
async def publish_meeting_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user:
        log_user_action(user.id, user.username, user.first_name, "STARTED_PUBLISH_PROCESS")
    
    # Проверяем, что это личный чат
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Используйте эту команду в личном чате с ботом!")
        return
    
    with db_manager.get_session() as session:
        meetings = get_user_meetings(session, user.id)
    
    if not meetings:
        await update.message.reply_text("У вас нет встреч для публикации.")
        return
    
    # Проверяем есть ли сохраненные группы
    with db_manager.get_session() as session:
        groups = get_user_groups(session, user.id)
    
    if not groups:
        await update.message.reply_text(
            "❌ У вас нет сохраненных групп.\n\n"
            "Сначала добавьте группу:\n"
            "1. Добавьте бота в группу\n"
            "2. Напишите в группе: /get_group_id\n"
            "3. Затем вернитесь и используйте /publish"
        )
        return
    
    # Показываем список встреч для публикации
    keyboard = []
    for meeting in meetings:
        if not meeting.published:
            keyboard.append([
                InlineKeyboardButton(
                    f"{meeting.title} ({meeting.date})", 
                    callback_data=f"select_{meeting.id}"
                )
            ])
    
    if not keyboard:
        await update.message.reply_text("❌ У вас нет неопубликованных встреч.")
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите встречу для публикации:", reply_markup=reply_markup)

# Обработка выбора встречи для публикации
@log_callback_handler
async def publish_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("select_"):
        meeting_id = int(data.split("_")[1])
        
        log_user_action(user.id, user.username, user.first_name, "SELECTED_MEETING_FOR_PUBLISH", f"meeting_id: {meeting_id}")
        
        # Сохраняем выбранную встречу
        context.user_data["publish_meeting_id"] = meeting_id
        
        # Показываем список сохраненных групп
        with db_manager.get_session() as session:
            groups = get_user_groups(session, user.id)
        
        if not groups:
            await query.edit_message_text(
                "❌ У вас нет сохраненных групп.\n\n"
                "Сначала добавьте группу:\n"
                "1. Добавьте бота в группу\n"
                "2. Напишите в группе: /get_group_id\n"
                "3. Затем вернитесь и используйте /publish"
            )
            return
        
        keyboard = []
        for group in groups:
            keyboard.append([
                InlineKeyboardButton(
                    f"{group.group_title or 'Группа'} (ID: {group.group_chat_id})", 
                    callback_data=f"publish_group_{meeting_id}_{group.id}"
                )
            ])
        
        # Добавляем кнопку для ввода ID группы вручную
        keyboard.append([
            InlineKeyboardButton(
                "📝 Ввести ID группы вручную", 
                callback_data=f"publish_manual_{meeting_id}"
            )
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите группу для публикации:\n\n"
            "💡 Вы можете добавить новую группу с помощью /get_group_id",
            reply_markup=reply_markup
        )

# Новый обработчик для выбора группы из сохраненных
@log_callback_handler
async def publish_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("publish_group_"):
        parts = data.split("_")
        if len(parts) >= 4:
            meeting_id = int(parts[2])
            group_db_id = int(parts[3])
            
            log_user_action(user.id, user.username, user.first_name, "SELECTED_SAVED_GROUP", 
                           f"meeting_id: {meeting_id}, group_db_id: {group_db_id}")
            
            try:
                # Получаем информацию о группе
                # Получаем информацию о группе
                with db_manager.get_session() as session:
                    group = get_group_by_id(session, group_db_id)   # было (session, user.id, group_db_id)
                    
                if not group or group.user_id != user.id:          # проверка принадлежности
                    await query.edit_message_text("❌ Группа не найдена или не принадлежит вам.")
                    return

                # Публикуем встречу
                await publish_meeting_to_group(update, context, user.id, meeting_id, group.group_chat_id)
                
                # Успешно завершаем - редактируем сообщение
                await query.edit_message_text(
                    "✅ Встреча опубликована! Проверьте её в выбранной группе.",
                    reply_markup=None
                )
                
            except Exception as e:
                logging.error(f"❌ PUBLISH_GROUP_HANDLER: Ошибка: {e}")
                await query.edit_message_text(
                    "❌ Произошла ошибка при публикации. Попробуйте еще раз.",
                    reply_markup=None
                )

# Обработчик для ручного ввода ID группы
@log_callback_handler
async def publish_manual_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("publish_manual_"):
        meeting_id = int(data.split("_")[2])
        
        log_user_action(user.id, user.username, user.first_name, "MANUAL_GROUP_INPUT", f"meeting_id: {meeting_id}")
        
        # Сохраняем выбранную встречу и переходим к ручному вводу
        context.user_data["publish_meeting_id"] = meeting_id
        context.user_data["publish_step"] = "group_id"
        
        await query.edit_message_text(
            "📝 Введите ID группы, куда опубликовать встречу:\n\n"
            "Как получить ID группы:\n"
            "1. Добавьте бота в группу\n"
            "2. Напишите в группе: /get_group_id\n"
            "3. Бот покажет ID группы\n"
            "4. Скопируйте это число и пришлите мне\n\n"
            "💡 Совет: используйте /get_group_id в группе чтобы автоматически сохранить её!"
        )

# Публикация встречи в группу
async def publish_meeting_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, meeting_id: int, group_chat_id: int):
    try:
        logging.info(f"🔄 PUBLISH_MEETING: Начало публикации meeting_id={meeting_id}, group_id={group_chat_id}")
        
        with db_manager.get_session() as session:
            meeting = get_meeting_by_id(session, user_id, meeting_id)
            
        if not meeting:
            logging.error(f"❌ PUBLISH_MEETING: Встреча не найдена")
            await update.message.reply_text("❌ Ошибка: встреча не найдена")
            cleanup_publish_context(context)
            return
        
        meeting_type = "🏢 Офлайн" if meeting.meeting_type == "offline" else "💻 Онлайн"
        creator_name = update.effective_user.full_name if update.effective_user else "Организатор"
        
        # Формируем сообщение для группы
        participants_block = "👥 Участники: пока никто не записался"

        group_text = f"""
    🎉 НОВАЯ ВСТРЕЧА!

    📌 Название: {meeting.title}
    📝 Описание: {meeting.description}
    📅 Дата: {meeting.date}
    ⏰ Время: {meeting.time}
    📍 Место: {meeting.location}
    🔰 Тип: {meeting_type}
    👤 Организатор: {creator_name}
    {participants_block}

    Для участия нажмите кнопку ниже 👇
    """
        
        keyboard = [
            [InlineKeyboardButton("✅ Я пойду!", callback_data=f"group_join_{user_id}_{meeting.id}_{group_chat_id}")],
            [InlineKeyboardButton("👥 Участники", callback_data=f"group_list_{user_id}_{meeting.id}_{group_chat_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отправляем в группу
        logging.info(f"📤 PUBLISH_MEETING: Отправка сообщения в группу {group_chat_id}")
        group_message = await context.bot.send_message(
            chat_id=group_chat_id,
            text=group_text,
            reply_markup=reply_markup
        )
        logging.info(f"✅ PUBLISH_MEETING: Сообщение отправлено, message_id={group_message.message_id}")
        
        # Обновляем статус встречи в БД
        logging.info(f"💾 PUBLISH_MEETING: Обновление БД")
        with db_manager.get_session() as session:
            update_meeting_field(session, meeting, "published", True)
            update_meeting_field(session, meeting, "published_in", group_chat_id)
            update_meeting_field(session, meeting, "group_message_id", group_message.message_id)
            
            # Сохраняем в групповых объявлениях
            create_group_announcement(session, group_chat_id, user_id, meeting.id)
            
            # Пытаемся сохранить группу (если еще не сохранена)
            try:
                chat = await context.bot.get_chat(group_chat_id)
                save_user_group(
                    session,
                    user_id,
                    group_chat_id,
                    chat.title,
                    chat.type
                )
                logging.info(f"💾 PUBLISH_MEETING: Группа сохранена: {chat.title}")
            except Exception as e:
                logging.warning(f"⚠️ PUBLISH_MEETING: Не удалось сохранить группу: {e}")
        
        # Очищаем временные данные
        cleanup_publish_context(context)
        
        # Логируем публикацию
        user = update.effective_user
        if user:
            log_user_action(user.id, user.username, user.first_name, "PUBLISHED_MEETING", 
                           f"meeting: '{meeting.title}', group: {group_chat_id}")
        
        logging.info(f"✅ PUBLISH_MEETING: Публикация завершена успешно")
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Встреча успешно опубликована в группе!"
        )
        
    except Exception as e:
        logging.error(f"❌ PUBLISH_MEETING: Ошибка при публикации: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"❌ Ошибка при публикации: {str(e)}"
        )

@log_callback_handler
async def group_join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    try:
        # Пытаемся ответить на callback
        await query.answer()
    except Exception as e:
        logging.warning(f"Не удалось ответить на callback: {e}")
        # Продолжаем выполнение даже если ответ не удался
    
    data = query.data
    user = query.from_user
    
    if data.startswith("group_join_"):
        parts = data.split("_")
        if len(parts) >= 5:
            try:
                creator_id = int(parts[2])
                meeting_id = int(parts[3])
                group_chat_id = int(parts[4])
                
                log_user_action(user.id, user.username, user.first_name, "JOINED_MEETING", 
                               f"meeting_id: {meeting_id}, group: {group_chat_id}")
                
                # Находим встречу в БД
                with db_manager.get_session() as session:
                    meeting = get_meeting_by_id(session, creator_id, meeting_id)
                    
                    if meeting:
                        # Инициализируем participants если это None
                        if meeting.participants is None:
                            meeting.participants = []
                        
                        participants = meeting.participants.copy()
                        
                        # Проверяем, не присоединился ли уже пользователь
                        user_already_joined = any(p["id"] == user.id for p in participants)
                        
                        if not user_already_joined:
                            participants.append({
                                "id": user.id,
                                "name": user.full_name,
                                "username": user.username
                            })
                            
                            update_meeting_participants(session, meeting, participants)
                            
                            # Обновляем сообщение в группе
                            await update_group_announcement(context, creator_id, meeting_id, group_chat_id)
                            
                            # Пытаемся уведомить пользователя
                            try:
                                await query.answer("✅ Вы присоединились к встрече!")
                            except:
                                pass
                        else:
                            try:
                                await query.answer("❌ Вы уже в списке участников!")
                            except:
                                pass
                    else:
                        try:
                            await query.answer("❌ Встреча не найдена!")
                        except:
                            pass
            except (ValueError, IndexError) as e:
                logging.error(f"Ошибка в group_join_handler: {e}")
                try:
                    await query.answer("❌ Ошибка при обработке запроса")
                except:
                    pass

# Показать список участников
async def show_participants_list(update: Update, context: ContextTypes.DEFAULT_TYPE, creator_id: int, meeting_id: int, group_chat_id: int):
    query = update.callback_query
    await query.answer()
    
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, creator_id, meeting_id)
        
    if not meeting:
        await query.edit_message_text("❌ Встреча не найдена!")
        return
    
    # Детальное логирование для отладки
    logging.info(f"🔍 PARTICIPANTS_DEBUG: meeting_id={meeting_id}, participants={meeting.participants}, count={len(meeting.participants)}")
    
    if not meeting.participants:
        await query.answer("📝 Пока никто не присоединился к встрече")
        return
    
    participants_text = "👥 Участники встречи:\n\n"
    for i, participant in enumerate(meeting.participants, 1):
        participants_text += f"{i}. {participant['name']}\n"
    
    await query.message.reply_text(participants_text)

# Обновление объявления в группе
async def update_group_announcement(context: ContextTypes.DEFAULT_TYPE, creator_id: int, meeting_id: int, group_chat_id: int):
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, creator_id, meeting_id)
    if not meeting:
        return

    meeting_type = "🏢 Офлайн" if meeting.meeting_type == "offline" else "💻 Онлайн"
    try:
        creator_chat = await context.bot.get_chat(creator_id)
        creator_name = creator_chat.full_name
    except:
        creator_name = "Организатор"

    participants_count = len(meeting.participants) if meeting.participants else 0

    participants_list = ""
    if meeting.participants:
        participants_list = "\n".join([f"• {p['name']}" for p in meeting.participants])
        participants_block = f"👥 Участники ({participants_count}):\n{participants_list}"
    else:
        participants_block = "👥 Участники: пока никто не записался"

    updated_text = f"""
🎉 НОВАЯ ВСТРЕЧА!

📌 Название: {meeting.title}
📝 Описание: {meeting.description}
📅 Дата: {meeting.date}
⏰ Время: {meeting.time}
📍 Место: {meeting.location}
🔰 Тип: {meeting_type}
👤 Организатор: {creator_name}
{participants_block}

Для участия нажмите кнопку ниже 👇
"""

    keyboard = [
        [InlineKeyboardButton("✅ Я пойду!", callback_data=f"group_join_{creator_id}_{meeting.id}_{group_chat_id}")],
        [InlineKeyboardButton("👥 Участники", callback_data=f"group_list_{creator_id}_{meeting.id}_{group_chat_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    old_message_id = meeting.group_message_id

    # 1. Пытаемся отредактировать старое сообщение (самый щадящий способ)
    if old_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=group_chat_id,
                message_id=old_message_id,
                text=updated_text,
                reply_markup=reply_markup
            )
            logging.info(f"✅ Отредактировано сообщение {old_message_id}")
            return  # успех, выходим
        except Exception as e:
            logging.warning(f"⚠️ Не удалось отредактировать {old_message_id}: {e}")

    # 2. Редактирование не вышло (или нет старого ID) — отправляем новое сообщение
    new_message = await context.bot.send_message(
        chat_id=group_chat_id,
        text=updated_text,
        reply_markup=reply_markup
    )
    logging.info(f"📩 Отправлено новое сообщение, id={new_message.message_id}")

    # 3. Обновляем ID в базе ДО удаления старого (чтобы при сбое не потерять новое)
    with db_manager.get_session() as session:
        meeting_to_update = get_meeting_by_id(session, creator_id, meeting_id)
        if meeting_to_update:
            update_meeting_field(session, meeting_to_update, "group_message_id", new_message.message_id)

    # 4. Удаляем старое сообщение (если есть права и оно существует)
    if old_message_id:
        try:
            await context.bot.delete_message(chat_id=group_chat_id, message_id=old_message_id)
            logging.info(f"🗑️ Старое сообщение {old_message_id} удалено")
        except Exception as e:
            logging.warning(f"❌ Не удалось удалить {old_message_id}: {e}. Проверьте права бота.")

@log_command
async def debug_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отладочная команда для проверки встреч"""
    user = update.effective_user
    
    with db_manager.get_session() as session:
        meetings = get_user_meetings(session, user.id)
    
    text = "🔍 ОТЛАДКА ВСТРЕЧ:\n\n"
    for i, meeting in enumerate(meetings, 1):
        participants_count = len(meeting.participants) if meeting.participants else 0
        text += f"{i}. {meeting.title}\n"
        text += f"   ID: {meeting.id}\n"
        text += f"   Участников: {participants_count}\n"
        text += f"   Участники: {meeting.participants}\n\n"
    
    await update.message.reply_text(text)

# Вспомогательные функции для очистки контекста
def cleanup_creation_context(context):
    """Очищает временные данные создания встречи"""
    keys_to_remove = ["meeting_type", "step", "title", "description", "date", "time", "location", "current_meeting_id"]
    for key in keys_to_remove:
        if key in context.user_data:
            del context.user_data[key]

def cleanup_publish_context(context):
    """Очищает временные данные публикации"""
    keys_to_remove = ["publish_meeting_id", "publish_step"]
    for key in keys_to_remove:
        if key in context.user_data:
            del context.user_data[key]

def cleanup_schedule_context(context):
    """Очищает временные данные отложенной публикации"""
    keys_to_remove = ["schedule_meeting_id", "schedule_group_id", "schedule_step"]
    for key in keys_to_remove:
        if key in context.user_data:
            del context.user_data[key]

def cleanup_edit_context(context):
    """Очищает временные данные редактирования"""
    keys_to_remove = ["edit_meeting_id", "edit_field", "edit_step"]
    for key in keys_to_remove:
        if key in context.user_data:
            del context.user_data[key]

# Функции для редактирования встреч
async def show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, meeting_id: int):
    query = update.callback_query
    await query.answer()
    
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user_id, meeting_id)
        
    if not meeting:
        await query.edit_message_text("❌ Встреча не найдена!")
        return
    
    keyboard = [
        [InlineKeyboardButton("📌 Название", callback_data=f"edit_field_{meeting_id}_title")],
        [InlineKeyboardButton("📝 Описание", callback_data=f"edit_field_{meeting_id}_description")],
        [InlineKeyboardButton("📅 Дата", callback_data=f"edit_field_{meeting_id}_date")],
        [InlineKeyboardButton("⏰ Время", callback_data=f"edit_field_{meeting_id}_time")],
        [InlineKeyboardButton("📍 Место/ссылка", callback_data=f"edit_field_{meeting_id}_location")],
        [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✏️ Редактирование встречи: {meeting.title}\n\n"
        "Выберите поле для редактирования:",
        reply_markup=reply_markup
    )

async def start_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, meeting_id: int, field: str):
    query = update.callback_query
    await query.answer()
    
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user_id, meeting_id)
        
    if not meeting:
        await query.edit_message_text("❌ Встреча не найдена!")
        return
    
    field_names = {
        "title": "название",
        "description": "описание",
        "date": "дату (ДД.ММ.ГГГГ)",
        "time": "время (ЧЧ:ММ)", 
        "location": "место/ссылку"
    }
    
    field_name = field_names.get(field, field)
    current_value = getattr(meeting, field, "")
    
    context.user_data["edit_meeting_id"] = meeting_id
    context.user_data["edit_field"] = field
    context.user_data["edit_step"] = "waiting_input"
    
    await query.edit_message_text(
        f"✏️ Введите новое {field_name}:\n\n"
        f"Текущее значение: {current_value}"
    )

async def handle_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str):
    meeting_id = context.user_data.get("edit_meeting_id")
    field = context.user_data.get("edit_field")
    
    if not meeting_id or not field:
        await update.message.reply_text("❌ Ошибка: данные редактирования не найдены")
        cleanup_edit_context(context)
        return
    
    # Проверяем существование встречи
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user.id, meeting_id)
        
        if not meeting:
            await update.message.reply_text("❌ Ошибка: встреча не найдена")
            cleanup_edit_context(context)
            return
        
        # Валидация введенных данных
        validation_error = None
        
        if field == "date":
            try:
                datetime.strptime(text, "%d.%m.%Y")
            except ValueError:
                validation_error = "❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ"
                    
        elif field == "time":
            try:
                datetime.strptime(text, "%H:%M")
            except ValueError:
                validation_error = "❌ Неверный формат времени. Используйте ЧЧ:ММ"
        
        if validation_error:
            await update.message.reply_text(validation_error)
            return
        
        # Обновляем поле встречи
        old_value = getattr(meeting, field)
        update_meeting_field(session, meeting, field, text)
        
        logging.info(f"✅ Обновлено поле {field}: '{old_value}' -> '{text}' для встречи '{meeting.title}'")
        
        log_user_action(user.id, user.username, user.first_name, "EDITED_MEETING_FIELD", 
                       f"meeting_id: {meeting_id}, field: {field}, new_value: {text[:50]}")
        
        # Обновляем в группе если встреча опубликована
        if meeting.published:
            try:
                await update_group_announcement(context, user.id, meeting_id, meeting.published_in)
                logging.info("✅ Объявление в группе обновлено")
            except Exception as e:
                logging.error(f"❌ Ошибка при обновлении группы: {e}")
                await update.message.reply_text("⚠️ Встреча обновлена, но не удалось обновить сообщение в группе")
    
    # Очищаем временные данные
    cleanup_edit_context(context)
    
    await update.message.reply_text(f"✅ {get_field_display_name(field)} успешно обновлено!")
    
    # Показываем меню редактирования снова
    keyboard = [
        [InlineKeyboardButton("📋 Вернуться к редактированию", callback_data=f"edit_select_{meeting_id}")],
        [InlineKeyboardButton("🏠 В главное меню", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Что хотите сделать дальше?", reply_markup=reply_markup)

def get_field_display_name(field):
    """Возвращает читаемое название поля"""
    field_names = {
        "title": "Название",
        "description": "Описание", 
        "date": "Дата",
        "time": "Время",
        "location": "Место/ссылка",
        "type": "Тип встречи"
    }
    return field_names.get(field, field.capitalize())

# Команда для редактирования встречи
@log_command
async def edit_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "STARTED_EDIT_MEETING")
    
    # Проверяем, что это личный чат
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Используйте эту команду в личном чате с ботом!")
        return
    
    with db_manager.get_session() as session:
        meetings = get_user_meetings(session, user.id)
    
    if not meetings:
        await update.message.reply_text("У вас нет встреч для редактирования.")
        return
    
    # Показываем список встреч для редактирования
    keyboard = []
    for meeting in meetings:
        status = "✅ Опубликована" if meeting.published else "📝 Черновик"
        keyboard.append([
            InlineKeyboardButton(
                f"{meeting.title} ({meeting.date}) - {status}", 
                callback_data=f"edit_select_{meeting.id}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите встречу для редактирования:",
        reply_markup=reply_markup
    )

# Команда для отложенной публикации
@log_command
async def schedule_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user:
        log_user_action(user.id, user.username, user.first_name, "STARTED_SCHEDULE_PROCESS")
    
    # Проверяем, что это личный чат
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Используйте эту команду в личном чате с ботом!")
        return
    
    with db_manager.get_session() as session:
        meetings = get_user_meetings(session, user.id)
    
    if not meetings:
        await update.message.reply_text("У вас нет встреч для отложенной публикации.")
        return
    
    # Проверяем есть ли сохраненные группы
    with db_manager.get_session() as session:
        groups = get_user_groups(session, user.id)
    
    if not groups:
        await update.message.reply_text(
            "❌ У вас нет сохраненных групп.\n\n"
            "Сначала добавьте группу:\n"
            "1. Добавьте бота в группу\n"
            "2. Напишите в группе: /get_group_id\n"
            "3. Затем вернитесь и используйте /schedule"
        )
        return
    
    # Показываем список встреч для отложенной публикации
    keyboard = []
    for meeting in meetings:
        if not meeting.published:
            status = "📝 Черновик"
            keyboard.append([
                InlineKeyboardButton(
                    f"{meeting.title} ({meeting.date}) - {status}", 
                    callback_data=f"schedule_select_{meeting.id}"
                )
            ])
    
    if not keyboard:
        await update.message.reply_text("❌ У вас нет неопубликованных встреч для отложенной публикации.")
        return
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Выберите встречу для отложенной публикации:",
        reply_markup=reply_markup
    )

# Обработка выбора встречи для отложенной публикации
@log_callback_handler
async def schedule_select_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("schedule_select_"):
        meeting_id = int(data.split("_")[2])
        
        log_user_action(user.id, user.username, user.first_name, "SELECTED_MEETING_FOR_SCHEDULE", f"meeting_id: {meeting_id}")
        
        # Сохраняем выбранную встречу
        context.user_data["schedule_meeting_id"] = meeting_id
        
        # Показываем список сохраненных групп
        with db_manager.get_session() as session:
            groups = get_user_groups(session, user.id)
        
        if not groups:
            await query.edit_message_text(
                "❌ У вас нет сохраненных групп.\n\n"
                "Сначала добавьте группу:\n"
                "1. Добавьте бота в группу\n"
                "2. Напишите в группе: /get_group_id\n"
                "3. Затем вернитесь и используйте /schedule"
            )
            return
        
        keyboard = []
        for group in groups:
            keyboard.append([
                InlineKeyboardButton(
                    f"{group.group_title or 'Группа'} (ID: {group.group_chat_id})", 
                    callback_data=f"schedule_group_{meeting_id}_{group.id}"
                )
            ])
        
        # Добавляем кнопку для ввода ID группы вручную
        keyboard.append([
            InlineKeyboardButton(
                "📝 Ввести ID группы вручную", 
                callback_data=f"schedule_manual_{meeting_id}"
            )
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите группу для отложенной публикации:\n\n"
            "💡 Вы можете добавить новую группу с помощью /get_group_id",
            reply_markup=reply_markup
        )

# Создание отложенной публикации
async def create_scheduled_post(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, meeting_id: int, group_chat_id: int, schedule_time: datetime):
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user_id, meeting_id)
        
    if not meeting:
        await update.message.reply_text("❌ Ошибка: встреча не найдена")
        cleanup_schedule_context(context)
        return
        
    # Создаем уникальный ID для задания
    job_id = f"{user_id}_{meeting_id}_{group_chat_id}_{int(schedule_time.timestamp())}"
    
    # Сохраняем информацию о отложенной публикации в БД
async def create_scheduled_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, meeting_id: int, group_chat_id: int, schedule_time: datetime):
    """Обработчик создания отложенной публикации"""
    with db_manager.get_session() as session:
        meeting = get_meeting_by_id(session, user_id, meeting_id)
        
    if not meeting:
        await update.message.reply_text("❌ Ошибка: встреча не найдена")
        cleanup_schedule_context(context)
        return
        
    # Создаем уникальный ID для задания
    job_id = f"{user_id}_{meeting_id}_{group_chat_id}_{int(schedule_time.timestamp())}"
    
    # Сохраняем информацию о отложенной публикации в БД
    with db_manager.get_session() as session:
        scheduled_post = create_scheduled_post_with_title(
            session,
            job_id,
            user_id,
            meeting_id,
            group_chat_id,
            schedule_time,
            meeting.title
        )
        
        # Пытаемся сохранить группу (если еще не сохранена)
        try:
            # Получаем информацию о группе
            chat = await context.bot.get_chat(group_chat_id)
            save_user_group(
                session,
                user_id,
                group_chat_id,
                chat.title,
                chat.type
            )
        except Exception as e:
            logging.warning(f"Не удалось сохранить информацию о группе: {e}")
    
    # Добавляем в активные проверки
    if user_id not in scheduled_posts:
        scheduled_posts[user_id] = []
    
    scheduled_posts[user_id].append({
        "job_id": job_id,
        "meeting_id": meeting_id,
        "group_chat_id": group_chat_id,
        "schedule_time": schedule_time,
        "meeting_title": meeting.title,
        "status": "scheduled",
        "created_at": datetime.now()
    })
    
    # Запускаем проверку отложенных публикаций
    start_schedule_checker(context.application)
    
    # Очищаем временные данные
    cleanup_schedule_context(context)
    
    log_user_action(user_id, update.effective_user.username, update.effective_user.first_name, 
                   "CREATED_SCHEDULED_POST", f"job_id: {job_id}, time: {schedule_time}")
    
    await update.message.reply_text(
        f"✅ Отложенная публикация создана!\n\n"
        f"📌 Встреча: {meeting.title}\n"
        f"📅 Дата публикации: {schedule_time.strftime('%d.%m.%Y %H:%M')}\n"
        f"📍 Группа: {group_chat_id}\n\n"
        f"Используйте /my_schedules для управления отложенными публикациями."
    )
# Функция для проверки и публикации отложенных постов
async def check_scheduled_posts(application):   # <-- добавили параметр
    while True:
        try:
            current_time = datetime.now()
            posts_to_publish = []
            for user_id, posts in scheduled_posts.items():
                for post in posts:
                    if post["status"] == "scheduled" and post["schedule_time"] <= current_time:
                        posts_to_publish.append((user_id, post))
            for user_id, post in posts_to_publish:
                await publish_scheduled_meeting(user_id, post, application)   # передаём application
            await asyncio.sleep(30)
        except Exception as e:
            logging.error(f"Ошибка в проверке отложенных публикаций: {e}")
            await asyncio.sleep(30)

# Функция для публикации отложенной встречи
async def publish_scheduled_meeting(user_id: int, post: dict, application) -> bool:
    try:
        meeting_id = post["meeting_id"]
        group_chat_id = post["group_chat_id"]
        job_id = post["job_id"]

        with db_manager.get_session() as session:
            meeting = get_meeting_by_id(session, user_id, meeting_id)
        if not meeting:
            logging.error(f"Встреча не найдена для {job_id}")
            return False

        meeting_type = "🏢 Офлайн" if meeting.meeting_type == "offline" else "💻 Онлайн"

        participants_block = "👥 Участники: пока никто не записался"

        text = f"""🎉 НОВАЯ ВСТРЕЧА! (АВТОМАТИЧЕСКАЯ ПУБЛИКАЦИЯ)

        📌 Название: {meeting.title}
        📝 Описание: {meeting.description}
        📅 Дата: {meeting.date}
        ⏰ Время: {meeting.time}
        📍 Место: {meeting.location}
        🔰 Тип: {meeting_type}
        👤 Организатор: Организатор
        {participants_block}

        Для участия нажмите кнопку ниже 👇"""

        keyboard = [
            [InlineKeyboardButton("✅ Я пойду!", callback_data=f"group_join_{user_id}_{meeting.id}_{group_chat_id}")],
            [InlineKeyboardButton("👥 Участники", callback_data=f"group_list_{user_id}_{meeting.id}_{group_chat_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Создаём транспорт с прокси (для новых версий httpx)
        transport = httpx.AsyncHTTPTransport(proxy=httpx.Proxy(url=PROXY_URL))
        async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
            # Отправка в группу
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": group_chat_id,
                    "text": text,
                    "reply_markup": reply_markup.to_dict() if reply_markup else None,
                    "parse_mode": "HTML"
                }
            )
            if resp.status_code != 200:
                logging.error(f"Ошибка отправки в группу: {resp.text}")
                return False
            result = resp.json()
            group_message_id = result["result"]["message_id"]

            # Обновление БД
            with db_manager.get_session() as session:
                update_meeting_field(session, meeting, "published", True)
                update_meeting_field(session, meeting, "published_in", group_chat_id)
                update_meeting_field(session, meeting, "group_message_id", group_message_id)
                update_scheduled_post_status(session, job_id, "published")
                create_group_announcement(session, group_chat_id, user_id, meeting.id)

            # Уведомление пользователю
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": user_id, "text": f"✅ Ваша встреча '{meeting.title}' опубликована!"}
            )

        # Удаляем из активных
        if user_id in scheduled_posts:
            scheduled_posts[user_id] = [p for p in scheduled_posts[user_id] if p["job_id"] != job_id]

        logging.info(f"✅ Опубликована встреча: {meeting.title}")
        return True

    except Exception as e:
        logging.error(f"Ошибка при авто-публикации {post.get('job_id')}: {e}")
        return False

# Запуск проверки отложенных публикаций
def start_schedule_checker(application):   # теперь принимает аргумент
    async def run_checker():
        await check_scheduled_posts(application)   # передаём дальше

    def run_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_checker())

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()

# Команда для просмотра отложенных публикаций
@log_command
async def my_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "VIEWED_MY_SCHEDULES")
    
    with db_manager.get_session() as session:
        posts = get_scheduled_posts(session, user.id)
    
    if not posts:
        await update.message.reply_text("У вас нет отложенных публикаций.")
        return
    
    text = "📋 Ваши отложенные публикации:\n\n"
    
    for i, post in enumerate(posts, 1):
        status_icons = {
            "scheduled": "⏰",
            "published": "✅",
            "cancelled": "❌"
        }
        
        status_texts = {
            "scheduled": "Запланирована",
            "published": "Опубликована", 
            "cancelled": "Отменена"
        }
        
        status_icon = status_icons.get(post.status, "❓")
        status_text = status_texts.get(post.status, "Неизвестно")
        
        text += f"{i}. {post.meeting_title}\n"
        text += f"   {status_icon} Статус: {status_text}\n"
        text += f"   📅 Время публикации: {post.schedule_time.strftime('%d.%m.%Y %H:%M')}\n"
        
        if post.status == "scheduled":
            time_left = post.schedule_time - datetime.now()
            if time_left.total_seconds() > 0:
                hours = int(time_left.total_seconds() // 3600)
                minutes = int((time_left.total_seconds() % 3600) // 60)
                text += f"   ⏱ Осталось: {hours}ч {minutes}м\n"
            
            # Добавляем кнопки управления для запланированных публикаций
            keyboard = []
            keyboard.append([
                InlineKeyboardButton("❌ Отменить публикацию", callback_data=f"cancel_schedule_{post.job_id}")
            ])
            keyboard.append([
                InlineKeyboardButton("🔄 Опубликовать сейчас", callback_data=f"publish_now_{post.job_id}")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup)
            text = ""  # Сбрасываем текст для следующего сообщения
        else:
            text += "\n"
    
    if text.strip():
        await update.message.reply_text(text)

# Обработка отмены отложенной публикации
@log_callback_handler
async def cancel_schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user = query.from_user
        
        logging.info(f"🔍 CANCEL_SCHEDULE_HANDLER: начал обработку callback: {data}")
        
        if data.startswith("cancel_schedule_"):
            job_id = data.split("_", 2)[2]
            
            logging.info(f"🔄 Попытка отменить отложенную публикацию: {job_id}")
            logging.info(f"👤 Пользователь: {user.id} ({user.username})")
            
            # Обновляем статус в БД
            try:
                with db_manager.get_session() as session:
                    logging.info(f"🔍 Ищу пост с job_id: {job_id}")
                    post = get_scheduled_post_by_job_id(session, job_id)
                    
                    if post:
                        logging.info(f"✅ Найден пост в БД: ID={post.id}, job_id={post.job_id}, статус={post.status}")
                        
                        # Пытаемся обновить статус
                        logging.info(f"🔄 Обновляю статус на 'cancelled'")
                        success = update_scheduled_post_status(session, job_id, "cancelled")
                        logging.info(f"📝 Результат обновления статуса: {success}")
                        
                        if success:
                            # Удаляем из активных проверок
                            if user.id in scheduled_posts:
                                old_count = len(scheduled_posts[user.id])
                                scheduled_posts[user.id] = [p for p in scheduled_posts[user.id] if p["job_id"] != job_id]
                                new_count = len(scheduled_posts[user.id])
                                logging.info(f"🗑️ Удален из активных проверок: было {old_count}, стало {new_count}")
                            
                            log_user_action(user.id, user.username, user.first_name, "CANCELLED_SCHEDULED_POST", f"job_id: {job_id}")
                            
                            # Обновляем сообщение
                            try:
                                logging.info("🔄 Пытаюсь обновить сообщение...")
                                await query.edit_message_text(
                                    "✅ Отложенная публикация отменена!",
                                    reply_markup=None  # Убираем кнопки
                                )
                                logging.info("✅ Сообщение успешно обновлено")
                            except Exception as e:
                                logging.error(f"❌ Ошибка при обновлении сообщения: {e}")
                                # Пытаемся отправить новое сообщение
                                await query.message.reply_text("✅ Отложенная публикация отменена!")
                        else:
                            logging.error("❌ Не удалось обновить статус в БД")
                            await query.edit_message_text("❌ Не удалось обновить статус публикации!")
                    else:
                        logging.error(f"❌ Пост не найден в БД для job_id: {job_id}")
                        
                        # Попробуем найти все посты пользователя для отладки
                        all_posts = get_scheduled_posts(session, user.id)
                        post_ids = [p.job_id for p in all_posts]
                        logging.info(f"🔍 Все посты пользователя {user.id}: {post_ids}")
                        
                        await query.edit_message_text("❌ Отложенная публикация не найдена в базе данных!")
                        
            except Exception as e:
                logging.error(f"❌ Ошибка при работе с БД: {e}")
                await query.edit_message_text("❌ Ошибка при работе с базой данных!")
                
        else:
            logging.warning(f"⚠️ Неизвестный callback pattern: {data}")
            
    except Exception as e:
        logging.error(f"❌ Критическая ошибка в cancel_schedule_handler: {e}")
        # Пытаемся отправить сообщение об ошибке
        try:
            if update and update.callback_query:
                await update.callback_query.message.reply_text("❌ Произошла ошибка при обработке запроса.")
        except:
            pass

# Обработчик удаления группы
@log_callback_handler
async def delete_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("delete_group_"):
        group_id = int(data.split("_")[2])
        
        with db_manager.get_session() as session:
            if deactivate_group(session, user.id, group_id):
                log_user_action(user.id, user.username, user.first_name, "DELETED_GROUP", f"group_id: {group_id}")
                await query.edit_message_text("✅ Группа удалена из списка!")
            else:
                await query.edit_message_text("❌ Не удалось найти группу для удаления.")

# Команда помощи
@log_command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        log_user_action(user.id, user.username, user.first_name, "REQUESTED_HELP")
    
    await update.message.reply_text(
        "🤖 Помощь по боту:\n\n"
        "/start - начать работу\n"
        "/new_meeting - создать новую встречу (в личном чате)\n"
        "/my_meetings - мои встречи\n"
        "/edit_meeting - редактировать встречу\n"
        "/publish - опубликовать встречу в группе\n"
        "/schedule - отложенная публикация\n"
        "/my_schedules - мои отложенные публикации\n"
        "/get_group_id - получить ID группы (автоматически сохраняет)\n"
        "/my_groups - мои сохраненные группы\n"
        "/manage_groups - управление группами\n"
        "/help - показать эту справку\n\n"
        "📌 Автоматическое сохранение групп:\n"
        "• Используйте /get_group_id в любой группе\n"
        "• Группа автоматически сохранится\n"
        "• При публикации и отложенной публикации выбирайте из сохраненных групп\n"
        "• Больше не нужно запоминать ID групп!"
    )

# Обработчик для выбора группы из сохраненных для отложенной публикации
@log_callback_handler
async def schedule_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("schedule_group_"):
        parts = data.split("_")
        if len(parts) >= 4:
            meeting_id = int(parts[2])
            group_db_id = int(parts[3])
            
            log_user_action(user.id, user.username, user.first_name, "SELECTED_SAVED_GROUP_FOR_SCHEDULE", 
                           f"meeting_id: {meeting_id}, group_db_id: {group_db_id}")
            
            # Получаем информацию о группе
            with db_manager.get_session() as session:
                group = get_group_by_id(session, group_db_id)
                
            if not group or group.user_id != user.id:
                await query.edit_message_text("❌ Группа не найдена или не принадлежит вам.")
                return
            
            # Сохраняем ID группы и переходим к выбору времени
            context.user_data["schedule_group_id"] = group.group_chat_id
            context.user_data["schedule_step"] = "datetime"
            
            await query.edit_message_text(
                "✅ Группа выбрана!\n\n"
                "Теперь введите дату и время публикации в формате:\n"
                "`ДД.ММ.ГГГГ ЧЧ:ММ`\n\n"
                "Пример: `25.12.2024 14:30`\n\n"
                "Время должно быть в будущем!"
            )

# Обработчик для ручного ввода ID группы для отложенной публикации
@log_callback_handler
async def schedule_manual_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("schedule_manual_"):
        meeting_id = int(data.split("_")[2])
        
        log_user_action(user.id, user.username, user.first_name, "MANUAL_GROUP_INPUT_SCHEDULE", f"meeting_id: {meeting_id}")
        
        # Сохраняем выбранную встречу и переходим к ручному вводу
        context.user_data["schedule_meeting_id"] = meeting_id
        context.user_data["schedule_step"] = "group_id"
        
        await query.edit_message_text(
            "📝 Введите ID группы, куда опубликовать встречу:\n\n"
            "Как получить ID группы:\n"
            "1. Добавьте бота в группу\n"
            "2. Напишите в группе: /get_group_id\n"
            "3. Бот покажет ID группы\n"
            "4. Скопируйте это число и пришлите мне\n\n"
            "💡 Совет: используйте /get_group_id в группе чтобы автоматически сохранить её!"
        )

# Обработчик для публикации отложенного поста сейчас
@log_callback_handler
async def publish_now_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("publish_now_"):
        job_id = data.split("_", 2)[2]
        
        logging.info(f"🔄 Попытка опубликовать отложенную публикацию сейчас: {job_id}")
        logging.info(f"🔍 Ищем пост с job_id: {job_id} для пользователя {user.id}")
        
        # Находим информацию о отложенной публикации в БД
        with db_manager.get_session() as session:
            post = get_scheduled_post_by_job_id(session, job_id)
            
            if post:
                logging.info(f"✅ Найден пост в БД: ID={post.id}, job_id={post.job_id}, статус={post.status}")
                
                if post.status == "scheduled":
                    # Создаем временный пост для публикации
                    temp_post = {
                        "job_id": post.job_id,
                        "meeting_id": post.meeting_id,
                        "group_chat_id": post.group_chat_id,
                        "schedule_time": post.schedule_time,
                        "meeting_title": post.meeting_title,
                        "status": "scheduled"
                    }
                    
                    logging.info(f"📤 Публикую встречу: meeting_id={post.meeting_id}, group_id={post.group_chat_id}")
                    
                    # Публикуем встречу немедленно
                    success = await publish_scheduled_meeting(user.id, temp_post)
                    
                    if success:
                        log_user_action(user.id, user.username, user.first_name, "PUBLISHED_SCHEDULED_POST_NOW", f"job_id: {job_id}")
                        await query.edit_message_text("✅ Встреча опубликована сейчас!")
                    else:
                        await query.edit_message_text("❌ Ошибка при публикации встречи!")
                else:
                    logging.warning(f"⚠️ Пост имеет статус '{post.status}', а не 'scheduled'")
                    await query.edit_message_text(f"❌ Публикация уже имеет статус: {post.status}")
            else:
                logging.error(f"❌ Пост не найден в БД для job_id: {job_id}")
                await query.edit_message_text("❌ Отложенная публикация не найдена в базе данных!")

# Обработчик удаления группы
@log_callback_handler
async def delete_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data.startswith("delete_group_"):
        group_id = int(data.split("_")[2])
        
        with db_manager.get_session() as session:
            if deactivate_group(session, user.id, group_id):
                log_user_action(user.id, user.username, user.first_name, "DELETED_GROUP", f"group_id: {group_id}")
                await query.edit_message_text("✅ Группа удалена из списка!")
            else:
                await query.edit_message_text("❌ Не удалось найти группу для удаления.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ошибки в боте"""
    error_msg = f"Ошибка при обработке обновления: {context.error}"
    logging.error(error_msg)
    
    # Логируем детали для отладки
    if update:
        if update.callback_query:
            logging.error(f"Callback data: {update.callback_query.data}")
        if update.message:
            logging.error(f"Message text: {update.message.text}")
    
    # НЕ отправляем сообщение об ошибке пользователю, если публикация прошла успешно
    # Вместо этого логируем ошибку для отладки
    
    # Только для критических ошибок отправляем сообщение
    if isinstance(context.error, Exception) and "publish" not in str(context.error).lower():
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Произошла ошибка при обработке запроса. Попробуйте еще раз."
                )
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение об ошибке: {e}")

# Основная функция
def main():
    # Логируем запуск бота
    logging.info("🤖 Бот запускается...")
    
    # Инициализируем базу данных
    try:
        db_manager.create_tables()
        with db_manager.get_session() as session:
            session.execute(text("UPDATE meetings SET participants = '[]'"))
            session.commit()
        logging.info("✅ База данных инициализирована")
    except Exception as e:
        logging.error(f"❌ Ошибка инициализации БД: {e}")
        return
    
    # Загружаем активные отложенные публикации из БД
    with db_manager.get_session() as session:
        posts = get_scheduled_posts(session)
        for post in posts:
            if post.status == "scheduled":
                if post.user_id not in scheduled_posts:
                    scheduled_posts[post.user_id] = []
                
                scheduled_posts[post.user_id].append({
                    "job_id": post.job_id,
                    "meeting_id": post.meeting_id,
                    "group_chat_id": post.group_chat_id,
                    "schedule_time": post.schedule_time,
                    "meeting_title": post.meeting_title,
                    "status": post.status,
                    "created_at": post.created_at
                })
    
    logging.info(f"✅ Загружено {sum(len(posts) for posts in scheduled_posts.values())} активных отложенных публикаций из БД")
    
    try:
        builder = Application.builder().token(BOT_TOKEN)
        if PROXY_URL:
            logging.info(f"🌐 Подключение через прокси: {PROXY_URL}")
            
            # Обычный транспорт для всех запросов, кроме getUpdates
            proxy_request = HTTPXRequest(
                proxy=PROXY_URL,
                read_timeout=60,
                connect_timeout=60,
                write_timeout=60
            )
            builder.request(proxy_request)
            
            # Отдельный транспорт для long polling (getUpdates) с большими таймаутами
            get_updates_request = HTTPXRequest(
                proxy=PROXY_URL,
                read_timeout=120,       # до 2 минут
                connect_timeout=60,
                write_timeout=60
            )
            builder.get_updates_request(get_updates_request)
        else:
            logging.info("ℹ️ Прокси не используется, прямое подключение")

        application = builder.build()
        
        # Добавляем обработчик ошибок
        application.add_error_handler(error_handler)
        
        # Основные команды
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("new_meeting", new_meeting))
        application.add_handler(CommandHandler("my_meetings", my_meetings))
        application.add_handler(CommandHandler("edit_meeting", edit_meeting))
        application.add_handler(CommandHandler("publish", publish_meeting_command))
        application.add_handler(CommandHandler("schedule", schedule_meeting))
        application.add_handler(CommandHandler("my_schedules", my_schedules))
        application.add_handler(CommandHandler("get_group_id", get_group_id))
        application.add_handler(CommandHandler("my_groups", my_groups))
        application.add_handler(CommandHandler("manage_groups", manage_groups))
        application.add_handler(CommandHandler("help", help_command))
        
        # Callback обработчики
        application.add_handler(CallbackQueryHandler(cancel_schedule_handler, pattern="^cancel_schedule_"))
        application.add_handler(CallbackQueryHandler(publish_now_handler, pattern="^publish_now_"))
        application.add_handler(CallbackQueryHandler(publish_group_handler, pattern="^publish_group_"))
        application.add_handler(CallbackQueryHandler(publish_manual_handler, pattern="^publish_manual_"))
        application.add_handler(CallbackQueryHandler(schedule_group_handler, pattern="^schedule_group_"))
        application.add_handler(CallbackQueryHandler(schedule_manual_handler, pattern="^schedule_manual_"))
        application.add_handler(CallbackQueryHandler(schedule_select_handler, pattern="^schedule_select_"))
        application.add_handler(CallbackQueryHandler(delete_group_handler, pattern="^delete_group_"))
        application.add_handler(CallbackQueryHandler(group_join_handler, pattern="^group_join_"))
        application.add_handler(CallbackQueryHandler(publish_select_handler, pattern="^select_"))
        application.add_handler(CallbackQueryHandler(button_handler, pattern="^(type_|confirm_|cancel|edit_select_|edit_field_|group_list_|groups_back)"))
        
        # Обработчики сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # Запускаем проверку отложенных публикаций
        start_schedule_checker(application)
        logging.info("✅ Проверка отложенных публикаций запущена")
        
        print("Бот запущен...")
        logging.info("✅ Бот успешно запущен")
        
        # Запускаем бота
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logging.error(f"❌ Критическая ошибка при запуске бота: {e}")
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    main()