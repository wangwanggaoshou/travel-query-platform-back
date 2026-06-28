from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional, List, Tuple
from app.models.scenic import Scenic
from app.database import get_sqlite_db
from app.utils.response import success, error, paginated


def _search_on_session(
    db: Session,
    kw: str,
    category: Optional[str] = None,
    region: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> Tuple[List[dict], int]:
    """在指定 Session 上执行景点搜索，返回 (items_list, total)。"""
    like = f"%{kw}%" if kw else "%"
    query = db.query(Scenic).filter(Scenic.is_active == 1)
    if kw:
        query = query.filter(
            or_(
                Scenic.name.like(like),
                Scenic.location.like(like),
                Scenic.description.like(like),
                Scenic.address.like(like),
            )
        )
    if category:
        query = query.filter(Scenic.category == category)
    if region:
        query = query.filter(Scenic.region == region)

    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    scenic_list = [{
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "region": item.region,
        "location": item.location,
        "price": item.price,
        "image": item.image,
        "description": item.description,
        "tags": item.tags or []
    } for item in items]
    return scenic_list, total


def _query_single_on_session(db: Session, kw: str) -> Optional[Scenic]:
    """按关键字在指定 Session 上精确或模糊查询单个景点。"""
    return db.query(Scenic).filter(
        Scenic.is_active == 1,
        or_(
            Scenic.name == kw,
            Scenic.name.contains(kw),
            Scenic.location.contains(kw),
        ),
    ).first()


def _merge_scenic_lists(list_a: list, list_b: list) -> list:
    """合并两个景点列表，按 id 去重。"""
    seen_ids: set[int] = set()
    merged: list = []
    for item in list_a + list_b:
        if item["id"] not in seen_ids:
            seen_ids.add(item["id"])
            merged.append(item)
    return merged


class ScenicService:
    @staticmethod
    def get_list(db: Session, page: int = 1, page_size: int = 10,
                 category: Optional[str] = None, region: Optional[str] = None,
                 sort_by: Optional[str] = None) -> dict:
        """列表查询：SQLite 旧数据 + MySQL 新数据合并。"""
        sqlite_db = get_sqlite_db()
        try:
            sqlite_items, sqlite_total = _search_on_session(sqlite_db, "", category, region, page=1, page_size=9999)
        finally:
            sqlite_db.close()

        mysql_items, mysql_total = _search_on_session(db, "", category, region, page=1, page_size=9999)

        all_items = _merge_scenic_lists(sqlite_items, mysql_items)
        if sort_by == "price":
            all_items.sort(key=lambda x: x["price"] or 0)
        else:
            all_items.sort(key=lambda x: x["id"], reverse=True)

        total = len(all_items)
        start = (page - 1) * page_size
        paged = all_items[start:start + page_size]
        return success(paginated(paged, total, page, page_size))

    @staticmethod
    def get_detail(db: Session, scenic_id: int) -> dict:
        """详情查询：先 SQLite 旧数据 → 再 MySQL 新数据。"""
        # 先查 SQLite
        sqlite_db = get_sqlite_db()
        try:
            scenic = sqlite_db.query(Scenic).filter(
                Scenic.id == scenic_id, Scenic.is_active == 1
            ).first()
        finally:
            sqlite_db.close()

        # 再查 MySQL
        if not scenic:
            scenic = db.query(Scenic).filter(
                Scenic.id == scenic_id, Scenic.is_active == 1
            ).first()

        if not scenic:
            return error(2001, "景点不存在")

        return success({
            "id": scenic.id,
            "name": scenic.name,
            "category": scenic.category,
            "region": scenic.region,
            "location": scenic.location,
            "price": scenic.price,
            "images": scenic.images or [],
            "description": scenic.description,
            "openingHours": scenic.opening_hours,
            "bestSeason": scenic.best_season,
            "tips": scenic.tips,
            "coordinates": {"lat": scenic.latitude, "lng": scenic.longitude} if scenic.latitude else None,
            "tags": scenic.tags or [],
        })

    @staticmethod
    def search(
        db: Session,
        keyword: str,
        page: int = 1,
        page_size: int = 10,
        category: Optional[str] = None,
        region: Optional[str] = None,
        discover: bool = False,
        city: Optional[str] = None,
    ) -> dict:
        """
        搜索景点：SQLite 旧数据 → MySQL 新数据 → 在线爬取。
        """
        kw = (keyword or "").strip()

        # 第1步：查 SQLite 旧数据
        sqlite_db = get_sqlite_db()
        try:
            items, total = _search_on_session(sqlite_db, kw, category, region, page, page_size)
        finally:
            sqlite_db.close()

        if total > 0:
            return success(paginated(items, total, page, page_size))

        # 第2步：查 MySQL 新数据
        items, total = _search_on_session(db, kw, category, region, page, page_size)
        created_new = False

        if discover and total == 0 and kw:
            from app.config import settings
            from app.services.scenic_discover import try_discover_scenic
            from crawler.amap_client import extract_city_from_location, resolve_amap_scenic
            import asyncio

            city_hint = (city or "").strip() or extract_city_from_location(kw)

            # 先用高德解析标准名称，再查 SQLite → MySQL
            if settings.AMAP_KEY:
                try:
                    amap = asyncio.run(resolve_amap_scenic(kw, city_hint or None))
                    if amap and amap.get("name"):
                        canon = amap["name"]
                        # 查 SQLite
                        sqlite_db2 = get_sqlite_db()
                        try:
                            items, total = _search_on_session(sqlite_db2, canon, category, region, page, page_size)
                            if total > 0:
                                sqlite_db2.close()
                                return success(paginated(items, total, page, page_size))
                        finally:
                            sqlite_db2.close()
                        # 查 MySQL
                        items, total = _search_on_session(db, canon, category, region, page, page_size)
                except Exception:
                    pass

            # 第3步：在线爬取 → 存入 MySQL
            if total == 0:
                _, created_new = try_discover_scenic(db, kw, city=city_hint or None)
                # 重新查询（优先用高德规范名再查）
                items, total = _search_on_session(db, kw, category, region, page, page_size)
                if total == 0 and settings.AMAP_KEY:
                    try:
                        amap = asyncio.run(resolve_amap_scenic(kw, city_hint or None))
                        if amap and amap.get("name"):
                            items, total = _search_on_session(db, amap["name"], category, region, page, page_size)
                    except Exception:
                        pass

        # 统一使用 _search_on_session 返回的 dict 列表
        scenic_list = items if isinstance(items, list) else []
        payload = paginated(scenic_list, total, page, page_size)
        if discover and created_new:
            payload = {**payload, "discoveredNew": True}
        return success(payload)

    @staticmethod
    def get_categories(db: Session) -> dict:
        """分类统计：合并 SQLite 和 MySQL。"""
        label_map = {
            "nature": "自然风光",
            "history": "历史古迹",
            "theme_park": "主题乐园",
            "beach": "海滨度假",
            "mountain": "山岳景观",
            "city": "城市观光",
            "none": "暂无分类",
        }
        counts: dict[str, int] = {}

        for session_factory in [get_sqlite_db, lambda: db]:
            if session_factory == get_sqlite_db:
                s = get_sqlite_db()
            else:
                s = db
            try:
                rows = (
                    s.query(Scenic.category, func.count(Scenic.id))
                    .filter(Scenic.is_active == 1)
                    .group_by(Scenic.category)
                    .all()
                )
                for cat, n in rows:
                    counts[cat] = counts.get(cat, 0) + n
            finally:
                if session_factory == get_sqlite_db:
                    s.close()

        categories = []
        for value, label in label_map.items():
            categories.append({"label": label, "value": value, "count": int(counts.get(value, 0))})
        for cat, n in counts.items():
            if cat not in label_map:
                categories.append({"label": cat, "value": cat, "count": int(n)})
        return success({"categories": categories})

    @staticmethod
    def get_hot(db: Session, limit: int = 6) -> dict:
        """热门景点：合并 SQLite 和 MySQL。"""
        all_items = []

        for factory in [get_sqlite_db, lambda: db]:
            s = get_sqlite_db() if factory == get_sqlite_db else db
            try:
                items = s.query(Scenic).filter(
                    Scenic.is_active == 1, Scenic.is_hot == 1
                ).order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(limit).all()

                if not items:
                    items = (
                        s.query(Scenic)
                        .filter(Scenic.is_active == 1)
                        .order_by(Scenic.view_count.desc(), Scenic.id.desc())
                        .limit(limit)
                        .all()
                    )
                for item in items:
                    all_items.append({
                        "id": item.id,
                        "name": item.name,
                        "category": item.category,
                        "location": item.location,
                        "price": item.price,
                        "image": item.image,
                        "description": item.description,
                    })
            finally:
                if factory == get_sqlite_db:
                    s.close()

        # 去重，取前 limit
        seen: set[str] = set()
        unique = []
        for item in all_items:
            key = f"{item['name']}-{item['location']}"
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return success({"list": unique[:limit]})
