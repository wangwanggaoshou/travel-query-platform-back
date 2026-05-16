import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from app.config import settings
from app.database import SessionLocal, engine, Base
from app.models.scenic import Scenic
from crawler.amap_client import (
    CATEGORY_NONE,
    amap_type_to_category,
    extract_city_from_location,
    resolve_amap_scenic,
)
from crawler.mediawiki import resolve_wikipedia, resolve_wikivoyage
from data.hot_cities import build_domestic_seed_items


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _is_placeholder_image(url: str | None) -> bool:
    if not url:
        return True
    u = url.lower()
    return "picsum.photos" in u or "placeholder" in u or "unsplash.com" in u


def reset_database() -> None:
    """删除 SQLite 库并重建表结构。"""
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite"):
        path = db_url.replace("sqlite:///", "")
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.dirname(__file__)), path)
        if os.path.isfile(path):
            try:
                os.remove(path)
                print(f"已删除数据库: {path}")
            except PermissionError:
                print(f"数据库文件被占用（请先停止后端），改为清空表: {path}")
                engine.dispose()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("数据库表已重建。")


async def _fetch_wiki_pair(name: str, location: str | None):
    async with httpx.AsyncClient(follow_redirects=True, timeout=40.0) as client:
        wiki = await resolve_wikipedia(client, name, location)
        voy = await resolve_wikivoyage(client, name, location)
        return wiki, voy


async def _enrich_from_amap(name: str, location: str | None) -> dict | None:
    if not settings.AMAP_KEY:
        print("  [提示] 未配置 AMAP_KEY，跳过高德 POI enrichment")
        return None
    city = extract_city_from_location(location)
    return await resolve_amap_scenic(name, city)


async def seed_one_scenic(db, item: dict) -> None:
    name = item["name"]
    location = item.get("location")

    amap = await _enrich_from_amap(name, location)
    search_name = (amap or {}).get("name") or name
    if amap:
        location = amap.get("location_text") or location

    wiki, voy = await _fetch_wiki_pair(search_name, location)

    desc = item.get("description")
    image = item.get("image")
    images = list(item.get("images") or [])
    lat = item.get("latitude")
    lon = item.get("longitude")
    address = item.get("address")
    category = item.get("category") or CATEGORY_NONE

    if amap:
        if amap.get("address"):
            address = amap["address"]
        if amap.get("latitude") is not None:
            lat = amap["latitude"]
        if amap.get("longitude") is not None:
            lon = amap["longitude"]
        category = amap_type_to_category(amap.get("typecode"), amap.get("type"))
        for url in amap.get("photos") or []:
            if url and url not in images:
                images.append(url)
        if amap.get("image"):
            image = amap["image"]
            if image not in images:
                images.insert(0, image)

    if wiki:
        desc = wiki.get("extract") or desc
        if wiki.get("thumbnail") and (_is_placeholder_image(image) or not image):
            image = wiki["thumbnail"]
            if wiki["thumbnail"] not in images:
                images = [wiki["thumbnail"], *images]
        if wiki.get("latitude") is not None and lat is None:
            lat = wiki.get("latitude")
        if wiki.get("longitude") is not None and lon is None:
            lon = wiki.get("longitude")
    elif voy and voy.get("thumbnail") and _is_placeholder_image(image):
        image = voy["thumbnail"]
        images = [voy["thumbnail"]]

    sid = item["id"]
    existing = db.query(Scenic).filter(Scenic.id == sid).first()
    fields = dict(
        name=(amap or {}).get("name") or name,
        category=category,
        region="domestic",
        location=location,
        address=address,
        price=float(item.get("price", 0.0)),
        image=image,
        images=images,
        description=desc or f"{name}位于{location or '国内'}，详情见在线资料。",
        opening_hours=item.get("opening_hours"),
        best_season=item.get("best_season"),
        tips=item.get("tips"),
        latitude=lat,
        longitude=lon,
        tags=item.get("tags", []),
        review_count=0,
        view_count=0,
        is_hot=item.get("is_hot", 0),
        is_active=1,
    )
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
    else:
        db.add(Scenic(id=sid, **fields))

    db.commit()
    src = "高德+维基" if amap else "维基"
    print(f"  景点「{fields['name']}」已导入（{src}）")


async def seed_scenics_async(db, data: list) -> None:
    print("正在通过高德地图 API 与维基媒体写入国内景点数据…")
    if not settings.AMAP_KEY:
        print("警告：未设置 AMAP_KEY，坐标与配图将主要依赖维基。")
    for i, item in enumerate(data):
        try:
            await seed_one_scenic(db, item)
            if i + 1 < len(data):
                await asyncio.sleep(1.2)
        except Exception as e:
            db.rollback()
            print(f"  景点 id={item.get('id')} 导入失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="初始化/重建旅途智览景点数据库")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="删除现有 SQLite 数据库后重新建表并爬取",
    )
    parser.add_argument(
        "--from-json",
        action="store_true",
        help="使用 data/seeds/scenic_seed.json 而非热门城市列表",
    )
    args = parser.parse_args()

    if args.reset:
        reset_database()
    else:
        print("初始化数据库…")
        Base.metadata.create_all(bind=engine)

    scenics = (
        load_json(os.path.join(os.path.dirname(__file__), "seeds", "scenic_seed.json"))
        if args.from_json
        else build_domestic_seed_items()
    )
    scenics = [s for s in scenics if s.get("region", "domestic") == "domestic"]

    db = SessionLocal()
    try:
        asyncio.run(seed_scenics_async(db, scenics))
        print(
            f"\n导入完成：共 {len(scenics)} 个国内景点，"
            "数据来自高德地图 API + 维基媒体。"
        )
    except Exception as e:
        print(f"导入失败: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
