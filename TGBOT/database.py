# database.py
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import logging

# Настройка логирования для базы данных
logger = logging.getLogger('database')

# Базовый класс для моделей
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class Meeting(Base):
    __tablename__ = 'meetings'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    meeting_type = Column(String(20), nullable=False)  # 'online' или 'offline'
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    date = Column(String(10), nullable=False)  # ДД.ММ.ГГГГ
    time = Column(String(5), nullable=False)   # ЧЧ:ММ
    location = Column(Text, nullable=False)    # Место или ссылка
    participants = Column(JSON, default=list)  # Список участников
    published = Column(Boolean, default=False)
    published_in = Column(Integer, nullable=True)  # ID группы публикации
    group_message_id = Column(Integer, nullable=True)  # ID сообщения в группе
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class GroupAnnouncement(Base):
    __tablename__ = 'group_announcements'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_chat_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    meeting_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    participants = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.now)

class ScheduledPost(Base):
    __tablename__ = 'scheduled_posts'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(100), unique=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    meeting_id = Column(Integer, nullable=False)
    group_chat_id = Column(Integer, nullable=False)
    schedule_time = Column(DateTime, nullable=False)
    meeting_title = Column(String(200), nullable=False)
    status = Column(String(20), default='scheduled')  # scheduled, published, cancelled
    created_at = Column(DateTime, default=datetime.now)
    published_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

class Group(Base):
    __tablename__ = 'groups'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    group_chat_id = Column(Integer, nullable=False)
    group_title = Column(String(200), nullable=True)
    group_type = Column(String(20), nullable=True)  # 'group', 'supergroup'
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

# Добавьте функции для работы с группами
def save_user_group(session, user_id: int, group_chat_id: int, group_title: str = None, group_type: str = None):
    """Сохраняет или обновляет группу пользователя"""
    # Проверяем, существует ли уже такая группа
    existing_group = session.query(Group).filter(
        Group.user_id == user_id,
        Group.group_chat_id == group_chat_id
    ).first()
    
    if existing_group:
        # Обновляем существующую группу
        existing_group.group_title = group_title
        existing_group.group_type = group_type
        existing_group.is_active = True
    else:
        # Создаем новую группу
        group = Group(
            user_id=user_id,
            group_chat_id=group_chat_id,
            group_title=group_title,
            group_type=group_type,
            is_active=True
        )
        session.add(group)
    
    session.commit()
    return True

def get_user_groups(session, user_id: int):
    """Возвращает все группы пользователя"""
    return session.query(Group).filter(
        Group.user_id == user_id,
        Group.is_active == True
    ).order_by(Group.created_at.desc()).all()

def get_group_by_id(session, user_id: int, group_id: int):
    """Возвращает группу по ID"""
    return session.query(Group).filter(
        Group.id == group_id,
        Group.user_id == user_id,
        Group.is_active == True
    ).first()

def deactivate_group(session, user_id: int, group_id: int):
    """Деактивирует группу (мягкое удаление)"""
    group = get_group_by_id(session, user_id, group_id)
    if group:
        group.is_active = False
        session.commit()
        return True
    return False

# Инициализация базы данных
class DatabaseManager:
    def __init__(self, database_url: str = None):
        if database_url is None:
            # Используем SQLite по умолчанию
            database_url = "sqlite:///meetings_bot.db"
        
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
    def create_tables(self):
        """Создает все таблицы в базе данных"""
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("✅ Таблицы базы данных созданы успешно")
        except Exception as e:
            logger.error(f"❌ Ошибка при создании таблиц: {e}")
            raise
    
    def get_session(self):
        """Возвращает сессию базы данных"""
        return self.SessionLocal()

# Глобальный экземпляр менеджера базы данных
db_manager = DatabaseManager()

# Функции для работы с пользователями
def get_or_create_user(session, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Получает или создает пользователя"""
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name
        )
        session.add(user)
        session.commit()
        logger.info(f"✅ Создан новый пользователь: {user_id}")
    return user

# Функции для работы с встречами
def create_meeting(session, user_id: int, meeting_data: dict):
    """Создает новую встречу"""
    try:
        # Явно устанавливаем пустой список участников
        participants = meeting_data.get("participants", [])
        
        logging.info(f"🔄 CREATE_MEETING: Создание встречи с участниками: {participants}")
        
        meeting = Meeting(
            user_id=user_id,
            meeting_type=meeting_data["type"],
            title=meeting_data["title"],
            description=meeting_data["description"],
            date=meeting_data["date"],
            time=meeting_data["time"],
            location=meeting_data["location"],
            participants=participants,  # Явно передаем список
            published=False,
            published_in=None,
            group_message_id=None
        )
        
        session.add(meeting)
        session.commit()
        
        logging.info(f"✅ CREATE_MEETING: Встреча создана с ID {meeting.id}, участников: {len(meeting.participants)}")
        return meeting
        
    except Exception as e:
        session.rollback()
        logging.error(f"❌ Ошибка при создании встречи: {e}")
        raise

def get_user_meetings(session, user_id: int):
    """Возвращает все встречи пользователя"""
    return session.query(Meeting).filter(Meeting.user_id == user_id).order_by(Meeting.created_at.desc()).all()

def get_meeting_by_id(session, user_id: int, meeting_id: int):
    """Возвращает встречу по ID"""
    return session.query(Meeting).filter(
        Meeting.id == meeting_id, 
        Meeting.user_id == user_id
    ).first()

def update_meeting_field(session, meeting, field: str, value):
    """Обновляет поле встречи"""
    if hasattr(meeting, field):
        setattr(meeting, field, value)
        session.commit()
        return True
    return False

def update_meeting_participants(session, meeting, participants: list):
    """Обновляет список участников встречи"""
    meeting.participants = participants
    session.commit()

# Функции для групповых объявлений
def create_group_announcement(session, group_chat_id: int, user_id: int, meeting_id: int, message_id: int):
    """Создает запись о групповом объявлении"""
    announcement = GroupAnnouncement(
        group_chat_id=group_chat_id,
        user_id=user_id,
        meeting_id=meeting_id,
        message_id=message_id,
        participants=[]
    )
    session.add(announcement)
    session.commit()
    return announcement

def get_group_announcement(session, group_chat_id: int, message_id: int):
    """Возвращает групповое объявление"""
    return session.query(GroupAnnouncement).filter(
        GroupAnnouncement.group_chat_id == group_chat_id,
        GroupAnnouncement.message_id == message_id
    ).first()

def get_group_announcements_by_meeting(session, user_id: int, meeting_id: int):
    """Возвращает групповые объявления для встречи"""
    return session.query(GroupAnnouncement).filter(
        GroupAnnouncement.user_id == user_id,
        GroupAnnouncement.meeting_id == meeting_id
    ).all()

# Функции для отложенных публикаций
def create_scheduled_post_with_title(session, job_id: str, user_id: int, meeting_id: int, group_chat_id: int, schedule_time: datetime, meeting_title: str):
    """Создает отложенную публикацию с названием встречи"""
    post = ScheduledPost(
        job_id=job_id,
        user_id=user_id,
        meeting_id=meeting_id,
        group_chat_id=group_chat_id,
        schedule_time=schedule_time,
        meeting_title=meeting_title,
        status="scheduled"
    )
    session.add(post)
    session.commit()
    return post

def get_scheduled_posts(session, user_id=None):
    """Получает отложенные публикации"""
    query = session.query(ScheduledPost)
    if user_id:
        query = query.filter(ScheduledPost.user_id == user_id)
    return query.order_by(ScheduledPost.schedule_time).all()

def get_scheduled_post_by_job_id(session, job_id):
    """Находит отложенную публикацию по job_id"""
    return session.query(ScheduledPost).filter(ScheduledPost.job_id == job_id).first()

def update_scheduled_post_status(session, job_id: str, status: str) -> bool:
    """Обновляет статус отложенной публикации"""
    try:
        logging.info(f"🔄 DATABASE: Обновление статуса для {job_id} на {status}")
        post = session.query(ScheduledPost).filter(ScheduledPost.job_id == job_id).first()
        
        if post:
            old_status = post.status
            post.status = status
            session.commit()
            logging.info(f"✅ DATABASE: Статус обновлен: {job_id} {old_status} -> {status}")
            return True
        else:
            logging.warning(f"❌ DATABASE: Пост не найден: {job_id}")
            return False
    except Exception as e:
        logging.error(f"❌ DATABASE: Ошибка при обновлении статуса {job_id}: {e}")
        session.rollback()
        return False

def delete_scheduled_post(session, job_id: str):
    """Удаляет отложенную публикацию"""
    post = session.query(ScheduledPost).filter(ScheduledPost.job_id == job_id).first()
    if post:
        session.delete(post)
        session.commit()
        return True
    return False
