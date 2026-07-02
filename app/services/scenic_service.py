from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import Optional
from app.models.scenic import Scenic
from app.utils.response import success, error, paginated

CATEGORY_LABEL_MAP = {
    "nature": "自然风光",
    "history": "历史古迹",
    "theme_park": "主题乐园",
    "beach": "海滨度假",
    "mountain": "山岳景观",
    "city": "城市观光",
    "none": "暂无分类",
}


def _category_label(cat: str | None) -> str:
    if not cat:
        return ""
    return CATEGORY_LABEL_MAP.get(cat, cat)


def _scenic_item(item: Scenic) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "categoryLabel": _category_label(item.category),
        "region": item.region,
        "location": item.location,
        "price": item.price,
        "image": item.image,
        "images": (item.images or [])[:8],
        "description": item.description,
        "tags": item.tags or [],
    }


class ScenicService:
    @staticmethod
    def get_list(db: Session, page: int = 1, page_size: int = 10,
                 category: Optional[str] = None, region: Optional[str] = None,
                 sort_by: Optional[str] = None) -> dict:
        query = db.query(Scenic).filter(Scenic.is_active == 1)
        if category:
            query = query.filter(Scenic.category == category)
        if region:
            query = query.filter(Scenic.region == region)
        if sort_by == "price_asc":
            query = query.order_by(Scenic.price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Scenic.price.desc())
        elif sort_by == "price":
            query = query.order_by(Scenic.price.asc())  # 兼容旧参数
        else:
            query = query.order_by(Scenic.id.desc())

        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        scenic_list = [_scenic_item(item) for item in items]
        return success(paginated(scenic_list, total, page, page_size))

    @staticmethod
    def get_detail(db: Session, scenic_id: int) -> dict:
        scenic = db.query(Scenic).filter(Scenic.id == scenic_id, Scenic.is_active == 1).first()
        if not scenic:
            return error(2001, "景点不存在")

        return success({
            "id": scenic.id,
            "name": scenic.name,
            "category": scenic.category,
            "categoryLabel": _category_label(scenic.category),
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
        sort_by: Optional[str] = None,
        discover: bool = False,
        city: Optional[str] = None,
    ) -> dict:
        kw = (keyword or "").strip()
        like = f"%{kw}%" if kw else "%"
        query = db.query(Scenic).filter(Scenic.is_active == 1)
        if kw:
            query = query.filter(
                or_(
                    Scenic.name.like(like),
                    Scenic.location.like(like),
                    Scenic.address.like(like),
                )
            )
        if category:
            query = query.filter(Scenic.category == category)
        if region:
            query = query.filter(Scenic.region == region)

        total = query.count()
        created_new = False

        if discover and total < 3 and kw:
            from app.config import settings
            from app.services.scenic_discover import try_discover_scenic, try_discover_multiple
            from crawler.amap_client import extract_city_from_location
            import asyncio

            city_hint = (city or "").strip() or extract_city_from_location(kw)

            if total == 0:
                # 完全无结果：精准发现单条
                _, created_new = try_discover_scenic(db, kw, city=city_hint or None)
            elif settings.AMAP_KEY:
                # 已有 1~2 条结果：批量扩展（如搜"长城"已有慕田峪，还需八达岭）
                try:
                    added = try_discover_multiple(db, kw, city=city_hint or None, max_new=5)
                    if added > 0:
                        created_new = True
                except Exception:
                    pass

            # 重新查询（包含新入库的）
            if created_new:
                query = db.query(Scenic).filter(Scenic.is_active == 1)
                if kw:
                    query = query.filter(
                        or_(
                            Scenic.name.like(like),
                            Scenic.location.like(like),
                            Scenic.address.like(like),
                        )
                    )
                if category:
                    query = query.filter(Scenic.category == category)
                if region:
                    query = query.filter(Scenic.region == region)
                total = query.count()

        # 排序
        if sort_by == "price_asc":
            query = query.order_by(Scenic.price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Scenic.price.desc())
        elif sort_by == "price":
            query = query.order_by(Scenic.price.asc())
        else:
            query = query.order_by(Scenic.id.desc())

        items = query.offset((page - 1) * page_size).limit(page_size).all()
        scenic_list = [_scenic_item(item) for item in items]
        payload = paginated(scenic_list, total, page, page_size)
        if discover and created_new:
            payload = {**payload, "discoveredNew": True}
        return success(payload)

    @staticmethod
    def get_categories(db: Session) -> dict:
        rows = (
            db.query(Scenic.category, func.count(Scenic.id))
            .filter(Scenic.is_active == 1)
            .group_by(Scenic.category)
            .all()
        )
        counts = {cat: n for cat, n in rows}
        categories = []
        for value, label in CATEGORY_LABEL_MAP.items():
            categories.append({"label": label, "value": value, "count": int(counts.get(value, 0))})
        for cat, n in counts.items():
            if cat not in CATEGORY_LABEL_MAP:
                categories.append({"label": cat, "value": cat, "count": int(n)})
        return success({"categories": categories})

    @staticmethod
    def get_hot(db: Session, limit: int = 6) -> dict:
        items = db.query(Scenic).filter(
            Scenic.is_active == 1, Scenic.is_hot == 1
        ).order_by(Scenic.view_count.desc(), Scenic.id.desc()).limit(limit).all()

        if not items:
            items = (
                db.query(Scenic)
                .filter(Scenic.is_active == 1)
                .order_by(Scenic.view_count.desc(), Scenic.id.desc())
                .limit(limit)
                .all()
            )

        scenic_list = [_scenic_item(item) for item in items]
        return success({"list": scenic_list})
