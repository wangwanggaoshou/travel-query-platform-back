"""搜索无结果时：高德 POI + 维基媒体写入国内景点数据。"""

from __future__ import annotations



import asyncio

import concurrent.futures

from typing import Optional, Tuple



import httpx

from sqlalchemy import func, or_

from sqlalchemy.orm import Session



from app.config import settings

from app.models.scenic import Scenic

from crawler.amap_client import (

    CATEGORY_NONE,

    amap_type_to_category,

    extract_city_from_location,

    resolve_amap_scenic,

)

from crawler.mediawiki import resolve_wikipedia, resolve_wikivoyage





def _guess_category(keyword: str, amap_type: str | None = None, typecode: str | None = None) -> str:

    if (amap_type or "").strip() or (typecode or "").strip():

        return amap_type_to_category(typecode, amap_type)

    return CATEGORY_NONE





def _is_domestic_poi(amap: dict) -> bool:

    city = (amap.get("cityname") or "").strip()

    if not city:

        return True

    overseas_markers = ("日本", "泰国", "法国", "美国", "英国", "韩国", "新加坡", "马来西亚")

    return not any(m in city for m in overseas_markers)





def _find_existing(db: Session, kw: str) -> Optional[Scenic]:

    return (

        db.query(Scenic)

        .filter(

            Scenic.is_active == 1,

            or_(Scenic.name == kw, Scenic.name.contains(kw), Scenic.location.contains(kw)),

        )

        .first()

    )





async def _fetch_all(kw: str, city: str | None):

    city_hint = city or extract_city_from_location(kw)

    amap = await resolve_amap_scenic(kw, city_hint) if settings.AMAP_KEY else None

    search_name = (amap or {}).get("name") or kw

    loc = (amap or {}).get("location_text")



    async with httpx.AsyncClient(follow_redirects=True, timeout=40.0) as client:

        wiki = await resolve_wikipedia(client, search_name, loc)

        voy = await resolve_wikivoyage(client, search_name, loc)



    return amap, wiki, voy





def _persist_discovered(

    db: Session,

    kw: str,

    amap: dict | None,

    wiki: dict | None,

    voy: dict | None,

) -> Tuple[Optional[Scenic], bool]:

    if amap and not _is_domestic_poi(amap):

        return None, False



    display_name = (amap or {}).get("name") or kw

    if display_name != kw:

        dup2 = db.query(Scenic).filter(Scenic.is_active == 1, Scenic.name == display_name).first()

        if dup2:

            return dup2, False



    desc_parts = []

    if wiki and wiki.get("extract"):

        desc_parts.append(wiki["extract"][:2000])

    elif amap and amap.get("address"):

        desc_parts.append(f"{display_name}位于{amap.get('location_text') or amap.get('address')}。")

    if voy and voy.get("extract"):

        ve = voy["extract"][:1500]

        we = (wiki.get("extract") if wiki else "") or ""

        if len(ve) > 80 and ve[:120] not in we:

            desc_parts.append(ve)

    description = "\n\n".join(desc_parts) if desc_parts else f"高德地图与国内公开资料整理的「{display_name}」。"



    images: list[str] = []

    image = None

    if amap:

        for u in amap.get("photos") or []:

            if u and u not in images:

                images.append(u)

        image = amap.get("image") or (images[0] if images else None)

    if wiki and wiki.get("thumbnail"):

        if wiki["thumbnail"] not in images:

            images.append(wiki["thumbnail"])

        if not image:

            image = wiki["thumbnail"]

    if voy and voy.get("thumbnail") and voy["thumbnail"] not in images:

        images.append(voy["thumbnail"])

        if not image:

            image = voy["thumbnail"]



    lat = (amap or {}).get("latitude") or (wiki.get("latitude") if wiki else None)

    lon = (amap or {}).get("longitude") or (wiki.get("longitude") if wiki else None)

    location = (amap or {}).get("location_text") or (wiki or {}).get("title") or kw

    address = (amap or {}).get("address")



    tags = ["搜索发现", "高德地图"]

    if amap and amap.get("id"):

        tags.append("POI")



    new_id = (db.query(func.max(Scenic.id)).scalar() or 0) + 1

    scenic = Scenic(

        id=new_id,

        name=display_name,

        category=_guess_category(

            display_name,

            (amap or {}).get("type"),

            (amap or {}).get("typecode"),

        ),

        region="domestic",

        location=location,

        address=address,

        price=0.0,

        image=image,

        images=images,

        description=description,

        opening_hours="以景区当日公告为准",

        best_season="四季皆宜",

        tips="坐标与地址来自高德地图，介绍来自维基媒体等公开资料，出行前请核实开放时间与票价。",

        latitude=lat,

        longitude=lon,

        tags=tags,

        review_count=0,

        view_count=0,

        is_hot=0,

        is_active=1,

    )

    db.add(scenic)

    db.commit()

    db.refresh(scenic)

    return scenic, True





async def try_discover_scenic_async(

    db: Session, keyword: str, city: str | None = None

) -> Tuple[Optional[Scenic], bool]:

    """异步：高德 POI + 维基 → 入库（供 FastAPI async 路由调用）。"""

    kw = (keyword or "").strip()

    if not kw or len(kw) > 80:

        return None, False



    try:

        dup = _find_existing(db, kw)

        if dup:

            return dup, False



        amap, wiki, voy = await _fetch_all(kw, city)

        return _persist_discovered(db, kw, amap, wiki, voy)

    except Exception:

        db.rollback()

        return None, False





def try_discover_scenic(

    db: Session, keyword: str, city: str | None = None

) -> Tuple[Optional[Scenic], bool]:

    """同步入口：在已有事件循环时于独立线程中执行 asyncio.run。"""

    try:

        asyncio.get_running_loop()

    except RuntimeError:

        return asyncio.run(try_discover_scenic_async(db, keyword, city))



    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

        future = pool.submit(asyncio.run, try_discover_scenic_async(db, keyword, city))

        return future.result(timeout=90)


