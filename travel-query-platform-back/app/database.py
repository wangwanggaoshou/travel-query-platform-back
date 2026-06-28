import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

# SQLite — 旧数据（只读查询）
sqlite_engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=settings.DEBUG,
)
SQLiteSession = sessionmaker(autocommit=False, autoflush=False, bind=sqlite_engine)

# MySQL — 新数据（读写）
mysql_engine = create_engine(
    settings.MYSQL_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    echo=settings.DEBUG,
)
MySQLSession = sessionmaker(autocommit=False, autoflush=False, bind=mysql_engine)

Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：返回 MySQL 会话（主库）。"""
    db = MySQLSession()
    try:
        yield db
    finally:
        db.close()


def get_sqlite_db():
    """返回 SQLite 会话（只读旧数据查询）。"""
    db = SQLiteSession()
    try:
        return db
    finally:
        db.close()


def init_db():
    """初始化数据库表。"""
    # SQLite 表（如果旧库文件丢失则创建）
    os.makedirs(os.path.dirname(settings.DATABASE_URL.replace("sqlite:///", "")), exist_ok=True)
    Base.metadata.create_all(bind=sqlite_engine)

    # MySQL 表
    Base.metadata.create_all(bind=mysql_engine)
