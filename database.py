"""
数据库模块：SQLAlchemy ORM + SQLite
Visit 表记录每次访问的设备、页面浏览情况
"""

import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./visits.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Visit(Base):
    """访问记录表"""

    __tablename__ = "visits"

    id          = Column(Integer, primary_key=True, index=True)
    visit_id    = Column(String(36), unique=True, index=True)  # UUID，前端用于后续更新
    ip          = Column(String(64), nullable=True)
    user_agent  = Column(Text, nullable=True)
    device_type = Column(String(20), nullable=True)            # mobile / tablet / desktop
    pages_viewed = Column(Text, nullable=True)                 # JSON 数组，如 "[0,1,2,3]"
    max_page    = Column(Integer, default=0)                   # 最远读到的页码（0-indexed）
    created_at  = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
