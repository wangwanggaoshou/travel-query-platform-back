from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings
import os

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate_sqlite_columns():
    """为已有 SQLite 库补充新增列（create_all 不会改表结构）。"""
    if "sqlite" not in settings.DATABASE_URL:
        return
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "guides" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("guides")}
    with engine.begin() as conn:
        if "source" not in cols:
            conn.execute(text("ALTER TABLE guides ADD COLUMN source VARCHAR(20) DEFAULT 'seed'"))
        if "topic" not in cols:
            conn.execute(text("ALTER TABLE guides ADD COLUMN topic VARCHAR(200)"))


def init_db():
    os.makedirs(os.path.dirname(settings.DATABASE_URL.replace("sqlite:///", "")), exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_columns()
