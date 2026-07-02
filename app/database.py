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

    # 清除现有数据库中的学校类景点
    import re
    def is_school_name(name: str) -> bool:
        if any(x in name for x in ("学校", "大学", "中学", "小学", "幼儿园", "学院", "校区", "分校", "美院", "附中", "附小", "大附")):
            return True
        if re.search(r"[一二三四五六七八九十百0-9]+中$", name):
            return True
        return False

    from sqlalchemy import text
    try:
        # 先用 SQL 做基础清洗
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM scenics WHERE name LIKE '%大学%' "
                "OR name LIKE '%学院%' OR name LIKE '%中学%' "
                "OR name LIKE '%小学%' OR name LIKE '%学校%' "
                "OR name LIKE '%校区%' OR name LIKE '%美院%' "
                "OR name LIKE '%商学院%' OR name LIKE '%幼儿园%'"
            ))
        # 再用 Python 加载其余项进行正则校验清洗
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), db_path)
        if os.path.isfile(db_path):
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM scenics")
            rows = cursor.fetchall()
            to_delete = [r[0] for r in rows if is_school_name(r[1])]
            if to_delete:
                cursor.executemany("DELETE FROM scenics WHERE id = ?", [(tid,) for tid in to_delete])
                conn.commit()
            conn.close()
        print("[init_db] 已成功移除现有数据库中的学校类景点。")
    except Exception as e:
        print(f"[init_db] 清理学校类景点数据失败: {e}")
