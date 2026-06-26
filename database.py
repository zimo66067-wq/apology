"""
数据库初始化模块
- 使用 SQLAlchemy ORM 连接 SQLite
- 定义 Visit 数据模型（访问记录表）
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./visits.db")

# SQLite 需要 connect_args 允许多线程访问
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Visit(Base):
    """访问记录表：每次 POST /api/visit 写入一行"""

    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, index=True)
    ip = Column(String(64), nullable=True)          # 来源 IP（可选）
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    """建表（表已存在时跳过）"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖注入：获取数据库会话，用完自动关闭"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
