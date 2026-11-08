# database.py
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, JSON, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime

logger = logging.getLogger('database')
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.now)

class Meeting(Base):
    __tablename__ = 'meetings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    meeting_type = Column(String(20), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    date = Column(String(10), nullable=False)
    time = Column(String(5), nullable=False)
    location = Column(Text, nullable=False)
    participants = Column(JSON, default=lambda: [])
    published = Column(Boolean, default=False)
    published_in = Column(Integer)
    group_message_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)

class Group(Base):
    __tablename__ = 'groups'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    group_chat_id = Column(Integer, nullable=False)
    group_title = Column(String(200))
    group_type = Column(String(20))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

class ScheduledPost(Base):
    __tablename__ = 'scheduled_posts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(100), unique=True)
    user_id = Column(Integer)
    meeting_id = Column(Integer)
    group_chat_id = Column(Integer)
    schedule_time = Column(DateTime)
    meeting_title = Column(String(200))
    status = Column(String(20), default='scheduled')
    created_at = Column(DateTime, default=func.now())

class DatabaseManager:
    def __init__(self, database_url="sqlite:///meetings_bot.db"):
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
    
    def create_tables(self):
        Base.metadata.create_all(bind=self.engine)

    @contextmanager
    def get_session(self):
        """Контекстный менеджер для управления сессиями базы данных."""
        session = self.SessionLocal()
        try:
            yield session
        except Exception as e:
            session.rollback()
            logger.error(f"Ошибка при выполнении операции в сессии: {e}")
            raise
        finally:
            session.close()

db_manager = DatabaseManager()

# ==========================================
# СУЩЕСТВУЮЩИЕ ФУНКЦИИ РАБОТЫ С ДАННЫМИ
# ==========================================

def get_or_create_user(session, user_id, username=None, first_name=None, last_name=None):
    user = session.query(User).filter(User.id == user_id).first()
    if not user:
        user = User(id=user_id, username=username, first_name=first_name, last_name=last_name)
        session.add(user)
        session.commit()
    return user

def create_meeting(session, user_id, data):
    meeting = Meeting(
        user_id=user_id,
        meeting_type=data["type"],
        title=data["title"],
        description=data["description"],
        date=data["date"],
        time=data["time"],
        location=data["location"],
        participants=[]
    )
    session.add(meeting)
    session.commit()
    return meeting

def update_meeting_participants(session, meeting, participants_list):
    meeting.participants = participants_list
    flag_modified(meeting, "participants") 
    session.commit()

def save_user_group(session, user_id, chat_id, title, gtype):
    group = session.query(Group).filter(Group.user_id == user_id, Group.group_chat_id == chat_id).first()
    if not group:
        group = Group(user_id=user_id, group_chat_id=chat_id, group_title=title, group_type=gtype)
        session.add(group)
    else:
        group.group_title = title
        group.is_active = True
    session.commit()

def get_user_groups(session, user_id):
    return session.query(Group).filter(Group.user_id == user_id, Group.is_active == True).all()

def get_meeting_by_id(session, user_id, mid):
    return session.query(Meeting).filter(Meeting.id == mid, Meeting.user_id == user_id).first()

def get_user_meetings(session, user_id):
    return session.query(Meeting).filter(Meeting.user_id == user_id).order_by(Meeting.created_at.desc()).all()


# ==========================================
# ДОБАВЛЕННЫЕ ФУНКЦИИ (ИСПРАВЛЯЮТ ОШИБКУ)
# ==========================================

def update_meeting_field(session, meeting, field, value):
    """Динамически обновляет конкретное поле у встречи."""
    setattr(meeting, field, value)
    if field == "participants":
        flag_modified(meeting, "participants")
    session.commit()
    return meeting

def create_group_announcement(session, meeting_id, group_chat_id, message_id):
    """Фиксирует публикацию анонса встречи в группе."""
    meeting = session.query(Meeting).filter(Meeting.id == meeting_id).first()
    if meeting:
        meeting.published = True
        meeting.published_in = group_chat_id
        meeting.group_message_id = message_id
        session.commit()
    return meeting

def get_group_announcement(session, meeting_id):
    """Получает данные о публикации встречи."""
    return session.query(Meeting).filter(Meeting.id == meeting_id, Meeting.published == True).first()

def create_scheduled_post_with_title(session, job_id, user_id, meeting_id, group_chat_id, schedule_time, meeting_title):
    """Создает запись о запланированном отложенном посте."""
    post = ScheduledPost(
        job_id=job_id,
        user_id=user_id,
        meeting_id=meeting_id,
        group_chat_id=group_chat_id,
        schedule_time=schedule_time,
        meeting_title=meeting_title,
        status='scheduled'
    )
    session.add(post)
    session.commit()
    return post

def get_scheduled_posts(session, user_id=None):
    """Возвращает список всех запланированных постов (опционально для юзера)."""
    query = session.query(ScheduledPost)
    if user_id:
        query = query.filter(ScheduledPost.user_id == user_id)
    return query.all()

def get_scheduled_post_by_job_id(session, job_id):
    """Ищет отложенный пост по ID задачи планировщика."""
    return session.query(ScheduledPost).filter(ScheduledPost.job_id == job_id).first()

def update_scheduled_post_status(session, job_id, status):
    """Обновляет статус задачи (например: 'sent', 'failed')."""
    post = session.query(ScheduledPost).filter(ScheduledPost.job_id == job_id).first()
    if post:
        post.status = status
        session.commit()
    return post

def get_group_by_id(session, group_id):
    """Ищет группу по её внутреннему ID или Telegram Chat ID."""
    return session.query(Group).filter((Group.id == group_id) | (Group.group_chat_id == group_id)).first()

def deactivate_group(session, chat_id, user_id=None):
    """Помечает группу как неактивную (когда бота удалили из канала/чата)."""
    query = session.query(Group).filter(Group.group_chat_id == chat_id)
    if user_id:
        query = query.filter(Group.user_id == user_id)
    group = query.first()
    if group:
        group.is_active = False
        session.commit()
    return group